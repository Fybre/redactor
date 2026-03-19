#!/bin/sh
# Start Ollama server in the background
ollama serve &
OLLAMA_PID=$!

# Wait for the API to be ready using ollama's own CLI
echo "Waiting for Ollama to start..."
until ollama list > /dev/null 2>&1; do
  sleep 2
done
echo "Ollama ready."

# Pull the configured model (fast no-op if already present)
MODEL="${OLLAMA_MODEL:-llama3.2:3b}"
echo "Pulling model: $MODEL"
ollama pull "$MODEL"
echo "Model ready."

# Hand control back to ollama serve
wait $OLLAMA_PID
