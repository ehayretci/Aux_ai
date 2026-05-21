#!/usr/bin/env python
"""Quick test of the three analysis agents."""

import sys
import json
from agents import advocate_run, critic_run, synthesiser_run

if len(sys.argv) < 2:
    print("Usage: python test_agents.py <screenshot_path>")
    print("Example: python test_agents.py screenshots/v1/state_1/01_intro.png")
    sys.exit(1)

image_path = sys.argv[1]

print(f"Testing agents on: {image_path}\n")
print("=" * 70)

print("\n📍 ADVOCATE (finding positives)...")
try:
    adv = advocate_run(image_path)
    print(json.dumps(adv, indent=2))
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("\n⚠️  CRITIC (finding problems)...")
try:
    crit = critic_run(image_path)
    print(json.dumps(crit, indent=2))
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("\n⚖️  SYNTHESISER (final verdict)...")
try:
    final = synthesiser_run(image_path, adv, crit)
    print(json.dumps(final, indent=2))
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("\n✅ All three agents ran successfully!\n")
