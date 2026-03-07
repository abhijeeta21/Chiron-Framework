:x = 10
:y = 0
:flag = 1
if :x == 10 [
    if :flag == 1 [
        :y = 50
        forward :y
    ] else [
        :y = 100
        backward :y
    ]
]
:z = :y