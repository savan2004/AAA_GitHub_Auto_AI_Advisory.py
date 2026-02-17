#!/bin/bash
# start.sh - Auto-restart script

echo "🚀 Starting AI Stock Advisor Bot..."

# Set environment variables (or load from .env)
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Infinite restart loop
while true; do
    echo "📅 $(date): Starting bot process..."
    
    # Run the bot
    python main.py
    
    # If we get here, bot crashed
    EXIT_CODE=$?
    echo "⚠️ Bot exited with code $EXIT_CODE at $(date)"
    
    # Wait before restart (exponential backoff)
    sleep 5
done