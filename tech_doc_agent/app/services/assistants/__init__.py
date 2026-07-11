"""Assistant prompts and dependency-bound registry factories.

Import concrete builders from their owning modules so importing `assistant_base`
does not eagerly construct or load every role definition.
"""
