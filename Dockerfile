# Use official Python lightweight image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY xau_algo/requirements.txt ./xau_algo/
RUN pip install --no-cache-dir -r xau_algo/requirements.txt

# Copy the entire project into the container
COPY . .

# Command to run the trading engine background worker
CMD ["python", "xau_algo/main.py", "--mode", "paper"]
