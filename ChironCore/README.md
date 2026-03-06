# Chiron-Slice: Backward Static Slicing Implementation

## 1. Overview and Objective
The **Chiron-Slice** module introduces backward static slicing capabilities to the Chiron Program Analysis Framework. It isolates the subset of a program that affects a specific variable at a specific program point (the slicing criterion). 

This is achieved by constructing a **Program Dependence Graph (PDG)**, which unifies both data dependencies (Def-Use chains) and control dependencies into a single directed graph . A backward reachability traversal on this graph determines the exact slice, effectively pruning irrelevant code and aiding in debugging and vulnerability analysis.

---

## 2. Core Architecture
The slicing engine operates in 7 distinct phases:
1. **Source Tracking**: Injecting line numbers into the Intermediate Representation (IR).
2. **Def-Use Extraction**: Recursively analyzing AST nodes to identify read/written variables.
3. **Data Dependency**: Executing a Reaching Definitions Data Flow Analysis (DFA).
4. **Control Dependency**: Computing Dominance Frontiers on a reversed Control Flow Graph (CFG).
5. **PDG Construction**: Fusing Data and Control edges into a `networkx.DiGraph`.
6. **Slicing Traversal**: Finding graph ancestors using Breadth-First Search (BFS).
7. **Visualization**: Rendering the colored slice using `pygraphviz`.

---

## 3. Implementation Details by Component

### Phase 1: Source Line Tracking in the AST
To map the user's slicing criterion (e.g., "Line 9") to the internal IR, the framework's AST needed to retain source code line numbers from the ANTLR parser.

* **`ChironAST/ChironAST.py`**: Modified the base `Instruction` class to include a `self.line_number = -1` attribute.
* **`ChironAST/builder.py`**: Updated the visitor methods (`visitAssignment`, `visitIfConditional`, `visitIfElseConditional`, `visitLoop`, `visitGotoCommand`, `visitMoveCommand`, `visitPenCommand`, `visitPauseCommand`) to extract the line number from the ANTLR context (`ctx.start.line`) and bind it to the generated AST node before returning the IR tuple.

### Phase 2: Def-Use Extraction (`slicing.py`)
A recursive helper function, `get_def_use(ast_node)`, was created to analyze any AST instruction and return a tuple of sets: `(defined_vars, used_vars)`. 
* **Assignments** write to their LHS and read from their RHS.
* **Conditionals and Arithmetic** read from their constituent expressions.
* **Commands** (Move, etc.) read from their argument expressions.

### Phase 3: Data Dependencies & Reaching Definitions (`slicing.py`)
Data dependencies track where a variable was last defined before being used. This requires a **Reaching Definitions** analysis.
* **Lattice Domain**: Implemented `ReachingDefDomain` using a Set Union for the `meet` operator.
* **Transfer Function**: `ReachingDefTransferFunction` implements the standard `OUT = GEN U (IN - KILL)` logic. Assignments generate a definition and kill older ones. To comply with Chiron's framework requirements, the transfer function returns a list (length 1 for standard blocks, length 2 for conditionals).
* **Worklist Algorithm Bypass**: Chiron v5.3 contains a bug in `dataFlowAnalysis.py` where `Interpreter.__init__` is missing a required `params` argument. To ensure the slicer is 100% robust, a standalone `_compute_reaching_defs_worklist()` method was implemented directly inside `ChironPDG` to bypass the broken framework module.

### Phase 4: Control Dependencies (`slicing.py`)
A node $Y$ is control-dependent on node $X$ if $X$ determines whether $Y$ executes. 
1. **Reversing the CFG**: The `nxgraph` from `irHandler.cfg` was reversed.
2. **Post-Dominance**: Located the actual `BasicBlock` object where `node.name == "END"` to use as the exit node.
3. **Dominance Frontiers**: Used `nx.dominance_frontiers` on the reversed graph. If $Y$ is in the dominance frontier of $X$ in the reversed graph, $X$ is control-dependent on $Y$ in the forward graph.

### Phase 5: PDG Construction (`slicing.py`)
The `ChironPDG` class constructs a `networkx.DiGraph`.
* **Nodes**: All instructions from `irHandler.ir` are added as nodes.
* **Data Edges**: Iterates through the IR. For every variable *used* in an instruction, a `type='data'` edge is added from the reaching definition node to the current node.
* **Control Edges**: Iterates through the control dependency map. A `type='control'` edge is added from the conditional instruction (the last instruction in the controlling block) to every instruction in the controlled block.

### Phase 6: Slicing Traversal (`slicing.py`)
The slicing criterion provides a target IR index. The slice is computed by invoking `nx.ancestors(graph, target_ir_idx)` to retrieve all nodes that have a path to the target node, representing all instructions that can affect the target variable.

### Phase 7: Visualization Engine (`slicing.py`)
The `dump_slice_pdg` function converts the NetworkX graph to an AGraph (`pygraphviz`) for rendering.
* **Node Filtering**: Compiler-injected loop counters (`__rep_counter_`) generated by `visitLoop` are scrubbed from the graph to prevent clutter.
* **Color Coding**: Sliced nodes are filled with light green (`#d4edda`); irrelevant nodes are faded to gray. 
* **Edge Styling**: Data edges are solid blue; Control edges are dashed red.

### Phase 8: CLI Integration (`chiron.py`)
The CLI was extended to expose the slicer to the user.
* **Argument Parser**: Added `-sl` / `--slice` expecting a `"line_number,variable_name"` string.
* **Variable Sanitization**: Handled Chiron's specific variable syntax by automatically prefixing the target variable with `:` if omitted by the user.
* **IR Mapping**: The CLI iterates backward through `irHandler.ir` to find the last instruction matching the target line number that either defines or uses the target variable, converting the user's line number criterion into a specific IR index for the PDG traversal.

---

## 4. Usage and Testing

**Command Syntax:**
```bash
python3 chiron.py -cfg_gen -sl "<line_number>,<variable_name>" <program.tl>
