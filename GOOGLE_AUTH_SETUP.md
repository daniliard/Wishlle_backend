# Google authentication — backend

## Railway variable

```env
GOOGLE_CLIENT_ID=000000000000-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com
```

Значення повинно повністю збігатися з `VITE_GOOGLE_CLIENT_ID` на Vercel.

Google Client Secret не потрібен для цієї схеми: браузер отримує ID token через Google Identity Services, а FastAPI перевіряє його офіційною бібліотекою `google-auth`.

## Directus users

Потрібні поля:

- `google_sub`: string, nullable, unique;
- `auth_provider`: string, nullable;
- `display_name`: string;
- `avatar_url`: string.

Endpoint:

```text
POST /api/auth/google
```

Body:

```json
{
  "id_token": "GOOGLE_ID_TOKEN"
}
```
