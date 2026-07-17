import os
import httpx

port = int(os.getenv("NODUS_PORT", "8000"))
url = f'http://localhost:{port}/feed.html'

r = httpx.get(url, timeout=5)
print(f'Status: {r.status_code}')
print(f'Has timeline-feed: {"timeline-feed" in r.text}')
print(f'Has event-reply: {"event-reply" in r.text}')
print(f'Has event-post: {"event-post" in r.text}')
print(f'Has back-to-latest: {"back-to-latest" in r.text}')
print(f'Has time-gap: {"time-gap" in r.text}')
print(f'Has tl-dot: {"tl-dot" in r.text}')
