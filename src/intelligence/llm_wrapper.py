"""
ARMS Intelligence Layer: Universal LLM Wrapper

This module provides a standardized interface for interacting with multiple 
LLM providers (Anthropic, Google, OpenAI). It parses structured JSON responses
for ARMS intelligence tasks, including SENTINEL Gate 1 & 2 analysis, 
TDC thesis reviews, and Systematic Scan Engine briefings.

"See it before the internet does."

Reference: ARMS FSD v1.1, Section 11.3
Reference: ARMS Module Specification — CDM + TDC | Addendum 2 to FSD v1.1
"""

import json
import os
from typing import Dict, Optional, Any, Literal

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class LLMWrapper:
    """
    Standardized wrapper for all ARMS intelligence calls.
    Supports Anthropic Claude, Google Gemini, and OpenAI ChatGPT.
    """

    def __init__(self, provider: Literal['anthropic', 'google', 'openai'] = 'google', model: Optional[str] = None):
        # Try to get provider/model from env
        env_model = os.environ.get('DEFAULT_LLM_MODEL')
        if env_model and not model:
            if 'gemini' in env_model:
                self.provider = 'google'
                self.model = env_model
            elif 'gpt' in env_model or 'o1' in env_model:
                self.provider = 'openai'
                self.model = env_model
            elif 'claude' in env_model:
                self.provider = 'anthropic'
                self.model = env_model
        else:
            self.provider = provider
            # Set default models based on provider if not explicitly passed
            if model:
                self.model = model
            else:
                if self.provider == 'anthropic':
                    self.model = "claude-3-5-sonnet-20241022"
                elif self.provider == 'google':
                    self.model = "gemini-3.1-pro-preview"
                elif self.provider == 'openai':
                    self.model = "gpt-5.5"

                
        print(f"[LLMWrapper] Initialized with provider: {self.provider.upper()}, model: {self.model}")

    def call(self, task_type: str, prompt: str, knowledge_base_query: Optional[str] = None) -> str:
        """
        Submits a prompt to the configured LLM API and returns the response.
        
        Args:
            task_type: 'sentinel_scan', 'thesis_review', 'lp_narrative', etc.
            prompt: The full structured prompt to send to the model.
            knowledge_base_query: Optional query to retrieve context for the prompt.
            
        Returns:
            The raw text response from the API (usually JSON).
        """
        # Retrieve knowledge base context if query provided
        if knowledge_base_query:
            try:
                from intelligence.kb_ingest import search_knowledge_base, format_kb_context
                kb_results = search_knowledge_base(knowledge_base_query, top_k=5)
                if kb_results:
                    kb_context = format_kb_context(kb_results)
                    prompt = f"Relevant ARMS Knowledge Base Context:\n{kb_context}\n\n---\n\n{prompt}"
            except Exception as e:
                print(f"[LLMWrapper] KB context retrieval failed (non-blocking): {e}")
        
        print(f"[LLMWrapper] Submitting {task_type} task to {self.model}...")
        
        if self.provider == 'anthropic':
            return self._call_anthropic(prompt)
        elif self.provider == 'google':
            return self._call_google(prompt)
        elif self.provider == 'openai':
            return self._call_openai(prompt)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _call_anthropic(self, prompt: str) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; refusing mock thesis response in live cycle.")
            
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON."}]
        )
        return response.content[0].text

    def _call_google(self, prompt: str) -> str:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set; refusing mock thesis response in live cycle.")
            
        from google import genai
        from google.genai import types
        
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=prompt + "\n\nReturn ONLY valid JSON.",
            config=types.GenerateContentConfig(temperature=0.1)
        )
        return response.text

    def _call_openai(self, prompt: str) -> str:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set; refusing mock thesis response in live cycle.")
            
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            response_format={ "type": "json_object" },
            messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON."}]
        )
        return response.choices[0].message.content

    def _mock_response(self, prompt: str) -> str:
        """Fallback mock for development if API keys are missing."""
        if "Google" in prompt and "MU" in prompt:
            return json.dumps({
                "tis_score": 6.2,
                "tis_label": "WATCH",
                "gates_affected": [2],
                "bear_case_evidence": "Google represents 15-20% of HBM demand. Legal proceedings create capex uncertainty.",
                "bull_case_rebuttal": "AI training demand is supply-constrained. Other hyperscalers likely absorb slack.",
                "recommended_action": "WATCH_FLAG"
            })
        elif "ALAB" in prompt and "Insider Sale" in prompt:
            return json.dumps({
                "tis_score": 5.5,
                "tis_label": "IMPAIRED",
                "gates_affected": [6],
                "bear_case_evidence": "CEO open-market sale of $75,000 may signal loss of confidence.",
                "bull_case_rebuttal": "Sale may be for personal reasons; size is small relative to total holdings.",
                "recommended_action": "PM_REVIEW"
            })
            
        return json.dumps({
            "tis_score": 8.5,
            "tis_label": "INTACT",
            "gates_affected": [],
            "bear_case_evidence": None,
            "bull_case_rebuttal": "Thesis remains strong.",
            "recommended_action": "MONITOR"
        })

# Default instance (Can be re-initialized by the caller if a different provider is desired)
llm_wrapper = LLMWrapper()
