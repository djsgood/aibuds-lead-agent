from lead_agent.models import OpenAIInterpreter
try:
    interpreter = OpenAIInterpreter(max_calls=1)
    result = interpreter.interpret([{"source_url":"https://aibudbots.ai/","quote":"Example test evidence: request a quote using a website form."}])
    print("OpenAI structured inference passed; hypotheses:", len(result))
except (ValueError, KeyError, TypeError) as exc:
    print("OpenAI validation failed:", type(exc).__name__)
    raise SystemExit(1) from None
finally:
    if "interpreter" in globals():
        interpreter.session.close()
