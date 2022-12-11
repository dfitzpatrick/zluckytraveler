FROM python:3.10-alpine
RUN apk update

RUN /usr/local/bin/python3.10 -m pip install --upgrade pip

WORKDIR /home/app
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .
CMD ["python3", "-m", "bot"]



