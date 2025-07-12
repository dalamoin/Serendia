Build the Container

gcloud builds submit --config cloudbuild.yaml --project=serendia

Deploy the application

gcloud run deploy po-automation \
  --image us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 10 \
  --project serendia

Log the Payloads

gcloud beta logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="po-automation" AND (textPayload:"✅" OR textPayload:"📩" OR textPayload:"📦" OR textPayload:"📄" OR textPayload:"⚠️")' \
  --project=serendia
