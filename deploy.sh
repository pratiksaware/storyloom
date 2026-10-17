#!/usr/bin/env bash
# Usage: PROJECT_ID=my-project ./deploy.sh
set -e
REGION=${REGION:-us-central1}; BUCKET=${PROJECT_ID}-storyloom-media
gcloud config set project $PROJECT_ID
gcloud services enable aiplatform.googleapis.com texttospeech.googleapis.com translate.googleapis.com \
  storage.googleapis.com bigquery.googleapis.com run.googleapis.com cloudbuild.googleapis.com
gsutil ls -b gs://$BUCKET >/dev/null 2>&1 || gsutil mb -l $REGION gs://$BUCKET
gcloud run deploy storyloom --source . --region $REGION --allow-unauthenticated --timeout 120 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,MEDIA_BUCKET=$BUCKET
# Grant the Cloud Run service account access
SA=$(gcloud run services describe storyloom --region $REGION --format 'value(spec.template.spec.serviceAccountName)')
[ -z "$SA" ] && SA="$(gcloud projects describe $PROJECT_ID --format 'value(projectNumber)')-compute@developer.gserviceaccount.com"
for R in aiplatform.user storage.objectAdmin bigquery.dataEditor bigquery.jobUser cloudtranslate.user; do
  gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$SA --role roles/$R -q >/dev/null
done
