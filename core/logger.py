import logging

def setup_logging():
    # Your bot logs
    logging.getLogger("discord").setLevel(logging.WARNING)  # suppress most discord logs
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)

    logging.basicConfig(
        level=logging.INFO,  # your logs stay INFO
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
