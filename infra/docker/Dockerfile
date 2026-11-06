FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY infra/docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p backend/data/hr \
    && curl -fL "https://raw.githubusercontent.com/ohhparkash/IBM-HR-Analytics-Employee-Attrition-Prediction-Model/04b59ba6e0424ab238b3db7f8c135df3c3104d35/IBM%20HR%20Employee%20Attrition%20Data.csv" \
       -o "backend/data/hr/IBM HR Employee Attrition Data.csv" \
    && echo "a5c31e38bd7fafc9bc333884eb181b06b41b8e5e488e8f7ccb27199fb3be7659  backend/data/hr/IBM HR Employee Attrition Data.csv" | sha256sum -c - \
    && curl -fL "https://raw.githubusercontent.com/tarockson/employee-retention-analytics/a290e31ed22784d7e77c0adad1e0a2cbffa0279e/data/sample_hris_anonymized.csv" \
       -o "backend/data/hr/sample_hris_anonymized.csv" \
    && echo "cf7b7b7f17e4ec163ba50181967f2faede90eb1b3d081b0c6c9fe0b971dd7079  backend/data/hr/sample_hris_anonymized.csv" | sha256sum -c - \
    && curl -fL "https://raw.githubusercontent.com/as16082023/Employee-Survey-Analysis/fb1b0ecdf0f4e48747c312446e3d42c004c5a0f7/HR%20Employee%20Survey%20Responses_Dataset.xlsx" \
       -o "backend/data/hr/HR Employee Survey Responses_Dataset.xlsx" \
    && echo "438d22b19c27b2f888bc6bfe20038d480c6879c84593d96a524d952064c0b013  backend/data/hr/HR Employee Survey Responses_Dataset.xlsx" | sha256sum -c -

COPY infra/docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
