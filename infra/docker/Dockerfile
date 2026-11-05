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
    && curl -fL "https://raw.githubusercontent.com/ohhparkash/IBM-HR-Analytics-Employee-Attrition-Prediction-Model/main/IBM%20HR%20Employee%20Attrition%20Data.csv" \
       -o "backend/data/hr/IBM HR Employee Attrition Data.csv" \
    && curl -fL "https://raw.githubusercontent.com/tarockson/employee-retention-analytics/main/data/sample_hris_anonymized.csv" \
       -o "backend/data/hr/sample_hris_anonymized.csv" \
    && curl -fL "https://raw.githubusercontent.com/as16082023/Employee-Survey-Analysis/main/HR%20Employee%20Survey%20Responses_Dataset.xlsx" \
       -o "backend/data/hr/HR Employee Survey Responses_Dataset.xlsx"

COPY infra/docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
