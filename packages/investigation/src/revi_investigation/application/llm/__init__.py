"""Schema-constrained LLM boundary: closed Pydantic response models,
versioned prompt templates, the payload guard, and the template renderer.

Everything the model returns is re-validated against pack/catalog/registry
content before any use — the LLM proposes, deterministic code disposes
(design §2.2).
"""
