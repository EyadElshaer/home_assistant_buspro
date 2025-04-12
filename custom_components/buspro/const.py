DOMAIN = "buspro"
CONF_HOST = "host"
CONF_PORT = "port"
DEFAULT_PORT = 6000

# Device configuration constants
CONF_SUBNET_ID = "subnet_id"
CONF_DEVICE_ID = "device_id"
CONF_CHANNEL = "channel"
CONF_DEVICE_TYPE = "device_type"
CONF_NAME = "name"

# Cover specific constants
CONF_OPENING_TIME = "opening_time"
DEFAULT_OPENING_TIME = 20
CONF_ADJUSTABLE = "adjustable"
DEFAULT_ADJUSTABLE = True

# Available device types
DEVICE_TYPES = [
    "cover",
    "light",
    "climate",
    "switch",
    "fan",
    "sensor",
    "universal_switch",
    "scene",
]