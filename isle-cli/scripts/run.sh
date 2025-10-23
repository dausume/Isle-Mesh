#!/bin/bash
# ./scripts/run.sh

# A generic approach for leveraging docker compose to run the app for people with little docker compose knowledge, 
# which ensures leftover containers are cleaned up each run.

# Timeout duration in seconds (e.g., 60 seconds)
TIMEOUT_DURATION=600

handle_error() {
    echo "An error occurred. Exiting."
    exit 1
}

# Removes leftover containers and images that accumulate every time you create a new build to save space.
# Run docker system prune with a timeout
if timeout $TIMEOUT_DURATION docker system prune -f; then
    echo "Docker system prune completed or timed out successfully."
else
    echo "Docker system prune did not complete within the timeout period."
    # You can choose to continue or exit here based on requirements
fi

# Re-builds the swift global application
sudo docker compose build || handle_error

# Launches the swift global application
sudo docker compose up || handle_error