#!/bin/bash

echo "🔧 Setting up your environment..."

# Check for Python 3
echo "Checking for Python 3..."
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
    echo "Python 3 found."
elif command -v python &>/dev/null; then
    # Fallback to 'python' if 'python3' isn't directly linked, but warn
    PYTHON_CMD="python"
    echo "Using 'python' command. Please ensure it refers to Python 3."
else
    echo "Error: Python 3 not found. Please install Python 3 to proceed."
    exit 1
fi

# Create and activate a virtual environment
if [ ! -d ".venv" ]; then
  echo "Creating and activating a virtual environment named 'venv'..."
  $PYTHON_CMD -m venv venv
fi

if [ $? -ne 0 ]; then
    echo "Error: Failed to create virtual environment. Do you have 'venv' module installed for Python?"
    echo "You might need to run: sudo apt-get install python3-venv (on Debian/Ubuntu) or equivalent."
    exit 1
fi

echo "🚀 Activating virtual environment..."
source venv/bin/activate

if [ $? -ne 0 ]; then
    echo "Error: Failed to activate virtual environment. Please check your setup."
    exit 1
fi
echo "Virtual environment activated."


# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip


# Install dependencies from requirements.txt
echo "📦 Installing Python dependencies from requirements.txt..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Error: Failed to install dependencies. Please check your internet connection or requirements.txt file."
        deactivate # Deactivate venv on error
        exit 1
    fi
    echo "All dependencies installed successfully."
else
    echo "Error: requirements.txt not found in the current directory. Please ensure it exists."
    deactivate # Deactivate venv on error
    exit 1
fi

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
  echo "⚠️  ffmpeg not found. Please install it manually:"
  echo "   • macOS: brew install ffmpeg"
  echo "   • Ubuntu: sudo apt install ffmpeg"
  echo "   • Windows: https://ffmpeg.org/download.html"
else
  echo "🎉 ffmpeg is installed."
fi

echo "✅ Setup complete. You're ready to go!"
