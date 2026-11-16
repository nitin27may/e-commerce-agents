"""Event autonomy (#94): budgets that let the system act alone inside a limit and ask a person outside it.

``policy`` loads and evaluates ``config/policies/autonomy.yaml`` (pure); ``engine`` turns an event into a logged,
atomic, at-most-once decision. See ``docs/event-autonomy.md``.
"""
