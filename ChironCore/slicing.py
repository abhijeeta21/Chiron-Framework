import copy
import networkx as nx
from networkx.drawing.nx_agraph import to_agraph
from lattice import Lattice, TransferFunction
import ChironAST.ChironAST as ChironAST

import math 

# ==========================================
# Phase 2: Def-Use Extraction Helper
# ==========================================

def get_def_use(ast_node):
    """Recursively extracts variables defined and used by an AST node."""
    defs = set()
    uses = set()
    
    if isinstance(ast_node, ChironAST.AssignmentCommand):
        defs.add(str(ast_node.lvar))
        _, rhs_uses = get_def_use(ast_node.rexpr)
        uses.update(rhs_uses)
        
    elif isinstance(ast_node, (ChironAST.BinArithOp, ChironAST.BinCondOp, ChironAST.AND, ChironAST.OR, ChironAST.LT, ChironAST.GT, ChironAST.LTE, ChironAST.GTE, ChironAST.EQ, ChironAST.NEQ, ChironAST.Sum, ChironAST.Diff, ChironAST.Mult, ChironAST.Div)):
        _, l_uses = get_def_use(ast_node.lexpr)
        _, r_uses = get_def_use(ast_node.rexpr)
        uses.update(l_uses)
        uses.update(r_uses)
        
    elif isinstance(ast_node, (ChironAST.UnaryArithOp, ChironAST.UMinus, ChironAST.NOT)):
        _, e_uses = get_def_use(ast_node.expr)
        uses.update(e_uses)
        
    elif isinstance(ast_node, ChironAST.Var):
        uses.add(str(ast_node.varname))
        
    elif isinstance(ast_node, ChironAST.ConditionCommand):
        _, c_uses = get_def_use(ast_node.cond)
        uses.update(c_uses)
        
    elif isinstance(ast_node, ChironAST.MoveCommand):
        _, e_uses = get_def_use(ast_node.expr)
        uses.update(e_uses)
        
    return defs, uses

# ==========================================
# Phase 3: Data Dependencies (Reaching Defs)
# ==========================================

class ReachingDefDomain(Lattice):
    def __init__(self, data=None):
        self.defs = set(data) if data else set()

    def meet(self, other):
        return ReachingDefDomain(self.defs.union(other.defs))

    def __eq__(self, other):
        return self.defs == other.defs

class ReachingDefTransferFunction(TransferFunction):
    def transferFunction(self, currBBIN, currBB):
        out_dict = copy.deepcopy(currBBIN) if currBBIN else {}
        
        for instr, ir_idx in currBB.instrlist:
            defs, _ = get_def_use(instr)
            for d in defs:
                out_dict[d] = ReachingDefDomain({ir_idx}) 
                
        if len(currBB.instrlist) > 0 and isinstance(currBB.instrlist[-1][0], ChironAST.ConditionCommand):
            return [out_dict, out_dict]
        return [out_dict]

class ReachingDefAnalysis():
    def __init__(self):
        self.transferFunctionInstance = ReachingDefTransferFunction()
        
    def initialize(self, currBB, isStartNode):
        return {} 

    def isEqual(self, dA, dB):
        if set(dA.keys()) != set(dB.keys()): return False
        for k in dA.keys():
            if not (dA[k] == dB[k]): return False
        return True

    def meet(self, predList):
        meetVal = {}
        for pred_dict in predList:
            for var_name, def_domain in pred_dict.items():
                if var_name not in meetVal:
                    meetVal[var_name] = ReachingDefDomain(def_domain.defs)
                else:
                    meetVal[var_name] = meetVal[var_name].meet(def_domain)
        return meetVal

# ==========================================
# Phase 4: Program Dependence Graph (PDG)
# ==========================================

class ChironPDG:
    def __init__(self, irHandler):
        self.pdg = nx.DiGraph(name="Program_Dependence_Graph")
        self.irHandler = irHandler
        self._build_graph()

    def _compute_control_deps(self):
        rev_cfg = self.irHandler.cfg.nxgraph.reverse(copy=True)
        end_node = next((n for n in rev_cfg.nodes() if n.name == "END"), None)
        if end_node is None:
            return {}
            
        dom_frontiers = nx.dominance_frontiers(rev_cfg, end_node)
        control_deps = {} 
        for node, frontier_set in dom_frontiers.items():
            for controlling_node in frontier_set:
                if node not in control_deps:
                    control_deps[node] = set()
                control_deps[node].add(controlling_node)
        return control_deps

    def _compute_reaching_defs_worklist(self):
        """Standalone Worklist Algorithm bypassing the framework bug."""
        cfg = self.irHandler.cfg
        analysis = ReachingDefAnalysis()
        
        bbIn = {node.name: analysis.initialize(node, node.name == "START") for node in cfg.nodes()}
        bbOut = {node.name: [] for node in cfg.nodes()}
        
        worklist = list(cfg.nodes())
        
        while worklist:
            currBB = worklist.pop(0)
            if currBB.name == "END": continue
            
            # Meet over predecessors
            inlist = []
            for pred in cfg.predecessors(currBB):
                label = cfg.get_edge_label(pred, currBB)
                out_vals = bbOut[pred.name]
                if out_vals:
                    if label != 'Cond_False':
                        inlist.append(out_vals[0])
                    elif len(out_vals) > 1:
                        inlist.append(out_vals[1])
            
            if inlist:
                bbIn[currBB.name] = analysis.meet(inlist)
                
            # Transfer
            oldOut = bbOut[currBB.name]
            newOut = analysis.transferFunctionInstance.transferFunction(bbIn[currBB.name], currBB)
            bbOut[currBB.name] = newOut
            
            # Check for changes
            changed = False
            if len(oldOut) != len(newOut):
                changed = True
            else:
                for old_v, new_v in zip(oldOut, newOut):
                    if not analysis.isEqual(old_v, new_v):
                        changed = True
                        break
                        
            # If changed, add successors back to worklist
            if changed:
                for succ in cfg.successors(currBB):
                    if succ not in worklist:
                        worklist.append(succ)
                        
        return bbIn

    def _build_graph(self):
        for idx, (instr, jump) in enumerate(self.irHandler.ir):
            self.pdg.add_node(idx, instruction=instr)

        # 2. Reaching Definitions (Bypassing broken framework interpreter)
        bbIn = self._compute_reaching_defs_worklist()

        for bb in self.irHandler.cfg:
            curr_state = {k: set(v.defs) for k, v in bbIn[bb.name].items()}
            
            for instr, ir_idx in bb.instrlist:
                defs, uses = get_def_use(instr)
                
                for use_var in uses:
                    if use_var in curr_state:
                        for def_idx in curr_state[use_var]:
                            self.pdg.add_edge(def_idx, ir_idx, type='data', var=use_var)
                
                for d in defs:
                    curr_state[d] = {ir_idx}

        control_deps = self._compute_control_deps()
        for controlled_bb, controlling_bbs in control_deps.items():
            for ctrl_bb in controlling_bbs:
                if ctrl_bb.name in ("START", "END"): continue
                if not ctrl_bb.instrlist or not controlled_bb.instrlist: continue
                
                cond_idx = ctrl_bb.instrlist[-1][1] 
                
                for _, dep_idx in controlled_bb.instrlist:
                    self.pdg.add_edge(cond_idx, dep_idx, type='control')

# ==========================================
# Phase 5 & 7: Slicing Traversal & Output
# ==========================================

def compute_backward_slice(pdg_wrapper, target_ir_idx):
    graph = pdg_wrapper.pdg
    if not graph.has_node(target_ir_idx):
        raise ValueError(f"Target IR index {target_ir_idx} not found in PDG.")
        
    ancestors = nx.ancestors(graph, target_ir_idx)
    slice_indices = set(ancestors)
    slice_indices.add(target_ir_idx)
    return sorted(list(slice_indices))

def dump_slice_pdg(pdg_wrapper, slice_indices, irHandler, filename="slice_output"):
    G = pdg_wrapper.pdg.copy()

    for node in list(G.nodes()):
        instr = irHandler.ir[node][0]
        if "__rep_counter_" in str(instr):
            G.remove_node(node)
            continue
            
        line_no = getattr(instr, 'line_number', '?')
        G.nodes[node]['label'] = f"L{node} (Line {line_no}):\n{instr}"
        G.nodes[node]['shape'] = 'box'
        G.nodes[node]['style'] = 'filled'

        if node in slice_indices:
            G.nodes[node]['color'] = 'darkgreen'
            G.nodes[node]['fillcolor'] = '#d4edda'
            G.nodes[node]['fontcolor'] = 'black'
        else:
            G.nodes[node]['color'] = 'lightgray'
            G.nodes[node]['fillcolor'] = '#f8f9fa'
            G.nodes[node]['fontcolor'] = 'gray'

    for u, v, data in G.edges(data=True):
        edge_type = data.get('type', '')
        if edge_type == 'data':
            data['color'] = 'blue'
            data['label'] = f"data ({data.get('var', '')})"
        elif edge_type == 'control':
            data['color'] = 'red'
            data['style'] = 'dashed'
            data['label'] = 'control'

    A = to_agraph(G)
    A.layout('dot')
    A.draw(filename + ".png")


# ==========================================
# Phase 8: Visual Slicing Geometry Engine
# ==========================================

def point_to_line_dist(px, py, x1, y1, x2, y2):
    """Calculates the shortest distance from a point to a finite line segment."""
    line_mag = math.hypot(x2 - x1, y2 - y1)
    if line_mag == 0:
        return math.hypot(px - x1, py - y1)

    # Calculate the dot product to find the projection of the point onto the line
    u = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / (line_mag ** 2)
    
    # If the projection is outside the segment, calculate distance to the closest endpoint
    if u < 0.0:
        return math.hypot(px - x1, py - y1)
    elif u > 1.0:
        return math.hypot(px - x2, py - y2)
    else:
        # Calculate distance to the projection point on the segment
        ix = x1 + u * (x2 - x1)
        iy = y1 + u * (y2 - y1)
        return math.hypot(px - ix, py - iy)

def get_closest_ir_from_coordinate(target_x, target_y, drawing_map):
    """Finds the IR index that drew the line segment closest to the target coordinates."""
    if not drawing_map:
        return -1
    
    min_dist = float('inf')
    closest_ir = -1
    
    for segment in drawing_map:
        x1, y1 = segment['start']
        x2, y2 = segment['end']
        
        dist = point_to_line_dist(target_x, target_y, x1, y1, x2, y2)
        
        if dist < min_dist:
            min_dist = dist
            closest_ir = segment['ir_idx']
            
    return closest_ir