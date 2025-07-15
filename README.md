# Step 1: Get your credentials

curl -s https://po-automation-68642982777.us-central1.run.app/auth/status | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data.get('status') == 'authenticated':
    print('ACCESS_TOKEN:', data.get('full_token'))
    print('REFRESH_TOKEN:', data.get('refresh_token'))
    print()
    print('COPY THESE FOR DEPLOYMENT:')
    print('PROCORE_ACCESS_TOKEN=' + data.get('full_token', ''))
    print('PROCORE_REFRESH_TOKEN=' + data.get('refresh_token', ''))
else:
    print('Not authenticated!')
"
# Step 2: Deploy Application

source .env && TIMESTAMP=$(date +%s) && echo "Persistent tokens deployment: ${TIMESTAMP}" && gcloud builds submit --config cloudbuild.yaml --substitutions=_TIMESTAMP=${TIMESTAMP} --project=serendia && gcloud run deploy po-automation --image us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation:${TIMESTAMP} --platform managed --region us-central1 --allow-unauthenticated --memory 512Mi --cpu 1 --timeout 300 --max-instances 10 --concurrency 80 --set-env-vars="PROCORE_CLIENT_ID=${PROCORE_CLIENT_ID},PROCORE_CLIENT_SECRET=${PROCORE_CLIENT_SECRET},PROCORE_REDIRECT_URI=${PROCORE_REDIRECT_URI},PROCORE_ENVIRONMENT=${PROCORE_ENVIRONMENT},PROCORE_ACCESS_TOKEN=PASTE_YOUR_ACCESS_TOKEN_HERE,PROCORE_REFRESH_TOKEN=PASTE_YOUR_REFRESH_TOKEN_HERE,DEPLOY_TIME=${TIMESTAMP}" --project=serendia

--Replace PASTE_YOUR_ACCESS_TOKEN_HERE and PASTE_YOUR_REFRESH_TOKEN_HERE with the actual tokens from Step 1.--
