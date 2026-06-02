# Production

Production uses `systemd` and Gunicorn.

The service should:

- run from `/var/www/rezervacije`
- load `/var/www/rezervacije/rezervacije.env` through `EnvironmentFile=`
- start `gunicorn -b 127.0.0.1:5000 wsgi:application`

Required environment variables are listed in `README.md` and in `rezervacije.env.example`.

For a CI-friendly docs check, run:

```bash
mkdocs build --strict
```
