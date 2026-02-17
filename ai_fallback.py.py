# ai_fallback.py - Multi-provider AI with fallbacks

import logging
from typing import Optional

logger = logging.getLogger(__name__)

class AIProvider:
    def __init__(self, groq_key=None, gemini_key=None, deepseek_key=None):
        self.groq_key = groq_key
        self.gemini_key = gemini_key
        self.deepseek_key = deepseek_key
        
        # Initialize clients if keys provided
        self.groq_client = None
        self.gemini_model = None
        
        if groq_key:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=groq_key)
            except ImportError:
                logger.warning("groq not installed")
        
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                self.gemini_model = genai.GenerativeModel("gemini-1.5-flash")
            except ImportError:
                logger.warning("google-generativeai not installed")
    
    def call_groq(self, prompt: str, max_tokens: int) -> Optional[str]:
        if not self.groq_client:
            return None
        
        try:
            models = ["llama3-8b-8192", "mixtral-8x7b-32768"]
            for model in models:
                try:
                    resp = self.groq_client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=0.35,
                        timeout=10
                    )
                    return resp.choices[0].message.content
                except Exception as e:
                    logger.warning(f"Groq {model} failed: {e}")
                    continue
        except Exception as e:
            logger.error(f"Groq error: {e}")
        
        return None
    
    def call_gemini(self, prompt: str, max_tokens: int) -> Optional[str]:
        if not self.gemini_model:
            return None
        
        try:
            resp = self.gemini_model.generate_content(
                prompt,
                generation_config={"max_output_tokens": max_tokens, "temperature": 0.35}
            )
            return resp.text
        except Exception as e:
            logger.error(f"Gemini error: {e}")
        
        return None
    
    def call(self, prompt: str, max_tokens: int = 600) -> str:
        """Try providers in sequence until one works"""
        
        # Try Groq first
        if self.groq_key:
            result = self.call_groq(prompt, max_tokens)
            if result:
                return result
        
        # Try Gemini next
        if self.gemini_key:
            result = self.call_gemini(prompt, max_tokens)
            if result:
                return result
        
        # All failed
        return ""