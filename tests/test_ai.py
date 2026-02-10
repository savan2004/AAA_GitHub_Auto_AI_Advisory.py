import unittest
from unittest.mock import patch
from bot_main import get_signal  # Import from bot_main.py

class TestAI(unittest.TestCase):
    @patch('google.genai.GenerativeModel.generate_content')
    def test_get
