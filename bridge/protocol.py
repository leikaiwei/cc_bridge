"""两端共享的协议常量：状态码、优先级、BLE 标识。

状态码同时就是优先级，数值越大越紧急。主机只向 ESP32 写 1 字节状态码，
颜色与动画由固件决定。
"""

# 状态码（= 优先级，越大越紧急）
STATE_IDLE = 0     # 空闲：暖白常亮
STATE_DONE = 1     # 完成：绿色常亮
STATE_WORKING = 2  # 工作中：Claude 橙色呼吸
STATE_CONFIRM = 3  # 需确认：黄色闪烁
STATE_ERROR = 4    # 报错：红色慢脉冲

# 状态名 → 状态码（hook 上报用状态名）
STATE_BY_NAME = {
    "idle": STATE_IDLE,
    "done": STATE_DONE,
    "working": STATE_WORKING,
    "confirm": STATE_CONFIRM,
    "error": STATE_ERROR,
}

# 特殊指令：会话结束，从聚合表移除
STATE_END = "end"

# BLE 标识（两端写死，所有电脑天然连同一个 ESP32）
DEVICE_NAME = "cc-bridge-kelvin"
SERVICE_UUID = "485a4423-1426-48d0-a1f2-0f4c992e6929"
CHAR_UUID = "45b7de33-287c-4d3a-af86-43ae3f45fa84"
