# Prints fresh dev-only values for backend/.env (META_APP_SECRET, META_VERIFY_TOKEN, INTERNAL_DEV_TOKEN,
# SIMULATOR_TOKEN, SEED_STAFF_PASSWORD). Local use only; never reuse them anywhere real.
# Copy SIMULATOR_TOKEN into simulator/.env.local as VITE_SIMULATOR_TOKEN.
$py = "import secrets as s; print('META_APP_SECRET=' + s.token_hex(32)); print('META_VERIFY_TOKEN=' + s.token_urlsafe(24)); print('INTERNAL_DEV_TOKEN=' + s.token_urlsafe(32)); print('SIMULATOR_TOKEN=' + s.token_urlsafe(18)); print('SEED_STAFF_PASSWORD=Restro-' + s.token_urlsafe(9))"
& py -3.12 -c $py
