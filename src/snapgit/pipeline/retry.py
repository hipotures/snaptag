def next_retry_attempt(attempts: int, max_attempts: int) -> str:
    return "retry" if attempts < max_attempts else "failed_terminal"
