import sys
import placeholders

print("KEYS in PLACEHOLDER_TEMPLATES:")
for k, v in placeholders.PLACEHOLDER_TEMPLATES.items():
    print(f"{k} -> {v}")

