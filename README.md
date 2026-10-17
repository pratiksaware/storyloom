# StoryLoom
Interactive stories that adapt to a learner's topic, age and language. Every scene is written by Gemini, illustrated by Imagen, translated by Cloud Translation and narrated by Text-to-Speech. Media goes to Cloud Storage, reader choices go to BigQuery, and the app runs on Cloud Run.

## Deploy
    PROJECT_ID=your-project ./deploy.sh

## Run locally
    gcloud auth application-default login
    pip install -r requirements.txt
    export GOOGLE_CLOUD_PROJECT=your-project MEDIA_BUCKET=your-bucket
    uvicorn main:app --reload --port 8080

Model names can be changed with GEMINI_MODEL and IMAGEN_MODEL env vars if your project has access to other versions.

## Extend
Veo: turn the final scene's image_prompt into a short clip with `ai.models.generate_videos`.
