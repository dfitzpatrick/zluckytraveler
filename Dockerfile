FROM python:3.10-alpine
RUN apk update

RUN /usr/local/bin/python3.10 -m pip install --upgrade pip

WORKDIR /home/bot
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
WORKDIR /home/bot

COPY . .
CMD ["python3", "-m", "bot"]



