# WordPress Migration Action

This directory contains a Python script and a GitHub Actions workflow to automate the migration of posts, categories, tags, authors, and media from one WordPress site to another using the self-hosted WP REST API.

## Features

- **Taxonomy Mapping:** Migrates categories and tags, preserving hierarchy.
- **Author Mapping:** Creates users on the destination site or maps them to existing ones.
- **Media Handling:** Automatically downloads featured images and inline content images and uploads them to the destination site.
- **Content Rewriting:** Replaces old image URLs inside the post content with the newly uploaded destination URLs.
- **Resilient Stateful Execution:** Keeps track of migrated items in a `migration_state.json` file. If the workflow times out or is interrupted, it can pick up right where it left off on the next run.

## Prerequisites

On **both** the Source and Destination WordPress sites, you need to generate an **Application Password**:
1. Log in as an Administrator.
2. Go to **Users > Profile**.
3. Scroll down to the **Application Passwords** section.
4. Enter a name (e.g., "GitHub Actions Migration") and click **Add New Application Password**.
5. Save the generated passwords securely.

## Setup in GitHub

To use the automated workflow, configure the following **Repository Secrets** within your GitHub repository settings (`Settings > Secrets and variables > Actions`):

- `SOURCE_WP_URL`: The full URL of the source site (e.g., `https://oldsite.com`)
- `SOURCE_WP_USER`: Your admin username on the source site.
- `SOURCE_WP_APP_PASSWORD`: The application password for the source site.
- `DEST_WP_URL`: The full URL of the destination site (e.g., `https://newsite.com`)
- `DEST_WP_USER`: Your admin username on the destination site.
- `DEST_WP_APP_PASSWORD`: The application password for the destination site.

## Running the Migration

1. Go to the **Actions** tab in this GitHub repository.
2. Select **WordPress Migration** from the left sidebar.
3. Click the **Run workflow** dropdown on the right.
4. Optionally enter a **Limit** (number of posts) if you want to test a small batch first (e.g., `5`). Leave it blank to migrate everything.
5. Click **Run workflow**.

### Artifacts (State file)

- After every run, the workflow uploads a `migration-state` artifact containing `migration_state.json`.
- The workflow automatically downloads the latest state artifact at the beginning of its next run, ensuring no duplicates are created.

## Google Search Indexing Removal

Before this will run successfully in your GitHub Actions pipeline, you need to set up the credentials:

#### Google Cloud Platform:

1. Go to your GCP console, create a project (or select an existing one), and enable the Web Search Indexing API.
2. Create a Service Account and generate a JSON Key. Download this file.

#### Google Search Console:

1. Go to Google Search Console for your source website.
2. Add the email address of the Service Account you just created and grant it Owner permissions. This is required for the Indexing API to accept requests for your domain.

#### GitHub Secrets:

1. Go to your GitHub repository -> Settings -> Secrets and variables -> Actions.
2. Create a new repository secret named GOOGLE_CREDENTIALS.
3. Paste the entire contents of the JSON Key file you downloaded from GCP into the secret value.

Once you have done these 3 steps, the workflow will automatically de-index newly migrated posts the next time it runs!