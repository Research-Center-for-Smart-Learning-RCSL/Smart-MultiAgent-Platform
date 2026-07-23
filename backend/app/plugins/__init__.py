"""First-party plugin registration sites imported at app startup.

Modules here register code (e.g. in-process activity validators) into
process-global registries owned by the bounded contexts, without those contexts
importing app-layer code — keeping the contexts domain-free (see
``contexts/activities/application/validators/registry.py``).
"""
