FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Playwright's bundled Node driver needs libatomic1, which slim images no longer ship.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Separate layers so a Playwright failure doesn't force a full pip reinstall.
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . ./

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
