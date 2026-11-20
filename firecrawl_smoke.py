from lead_agent.models import OpenAIInterpreter
try:
    interpreter = OpenAIInterpreter(max_calls=1)
    result = interpreter.interpret([{"source_url":"https://aibudbots.ai/","quote":"Example test evidence: request a quote using a website form."}])
    print("OpenAI structured inference passed; hypotheses:", len(result))
except (ValueError, KeyError, TypeError) as exc:
    import re
    message = str(exc)
    safe = message if re.fullmatch(r"OpenAI HTTP [0-9]{3}|OPENAI_API_KEY is not configured|OpenAI network failure|OpenAI response incomplete|OpenAI request limit reached|Evidence exceeds input limit", message) else type(exc).__name__
    print("OpenAI validation failed:", safe, flush=True)
    raise SystemExit(1) from None
finally:
    if "interpreter" in globals():
        interpreter.session.close()
