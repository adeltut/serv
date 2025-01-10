FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y wget git

# Клонируем репозиторий с кодом
RUN git clone https://github.com/adeltut/serv.git .

# Удаляем .git папку
RUN rm -rf .git

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-u", "main.py"]
