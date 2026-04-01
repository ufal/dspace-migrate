# Database configuration settings for robustness and performance

# Connection settings
DB_CONNECT_TIMEOUT = 30  # seconds
DB_KEEPALIVES_IDLE = 600  # seconds (10 minutes)
DB_KEEPALIVES_INTERVAL = 30  # seconds
DB_KEEPALIVES_COUNT = 3  # failed keepalives before connection is dead

# Retry settings
DB_MAX_RETRIES = 3
DB_RETRY_BASE_DELAY = 5  # seconds
DB_RETRY_MAX_DELAY = 60  # seconds

# Chunking settings
DB_CHUNK_SIZE = 50000  # rows per chunk for large tables
DB_LARGE_TABLE_THRESHOLD = 100000  # rows to trigger chunking
DB_CHUNK_DELAY = 0.1  # seconds between chunks to avoid overwhelming DB

# Logging
DB_LOG_CHUNK_PROGRESS = True
DB_LOG_RETRY_ATTEMPTS = True
