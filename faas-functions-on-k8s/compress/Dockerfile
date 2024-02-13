# Use an official Python runtime as a parent image
FROM python:3.8

# Define the directory to place the application code
ENV APP_DIR /usr/src/app

# Set the working directory in the container to APP_DIR
WORKDIR ${APP_DIR}

# Copy the Python script into the container at APP_DIR
COPY . ${APP_DIR}/

# Install the required Python package(s)
RUN pip install --no-cache-dir lorem

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Set environment variable to ensure Python output is set straight
# to the terminal without being first buffered and that Python
# does not try to write .pyc files
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Define the command to run the script
CMD ["python", "web-server-compress.py"]
