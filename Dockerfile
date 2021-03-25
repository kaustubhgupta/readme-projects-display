FROM python:3

RUN pip install -Iv PyGithub==1.53

ENTRYPOINT ["python", "main-script.py"]