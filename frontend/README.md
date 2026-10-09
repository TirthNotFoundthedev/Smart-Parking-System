# Parking frontend

A static site with no build step. Pages: `index.html` (landing), `user/`, `guard/`, `sensor/`. Shared styles and API helper live in `shared/`.

## Point the site at the backend

Edit `config.js` and set `API_BASE_URL` to the backend's address, with no trailing slash:

```js
window.APP_CONFIG = { API_BASE_URL: "https://your-backend.example.com" };
```

The backend must allow the site's origin through its `CORS_ORIGINS` environment variable, for example `https://your-user.github.io` or `https://your-site.vercel.app`. Include the scheme and omit any path.

## Run locally

```sh
cd frontend
python -m http.server 8080
```

Open http://localhost:8080. Add the origin `http://localhost:8080` to the backend's `CORS_ORIGINS` if the backend is running elsewhere.

## Deploy

All links are relative, so the site works from a domain root or a sub-path.

**Vercel:** import the repository and set Root Directory to `frontend`. No build command or output directory is needed. `vercel.json` is included.

**Netlify:** import the repository and set Base directory to `frontend`. Leave the build command empty and the publish directory as `.`. `netlify.toml` is included.

**GitHub Pages:** the workflow is at `.github/workflows/pages.yml` in the repository root. In the repository settings under Pages, set Source to "GitHub Actions". Pushes to `main` deploy the `frontend/` folder. `.nojekyll` is included so files are served as-is.
