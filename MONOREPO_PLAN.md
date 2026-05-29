# Plan de migración a monorepo

## Estructura objetivo

```
FlyPromocionesAdmin/
├── CLAUDE.md                    ← contexto unificado del sistema
├── .claude/
│   ├── settings.json
│   ├── rules/
│   │   ├── api-conventions.md   ← reglas del backend
│   │   └── frontend-conventions.md ← reglas del dashboard
│   └── commands/
│       ├── deploy-backend.md
│       ├── deploy-dashboard.md
│       └── db-check.md
├── backend/                     ← todo FlyBackend actual
│   ├── app/
│   ├── alembic/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── requirements.txt
│   └── ...
├── dashboard/                   ← todo FlyMarketingDashboard actual
│   ├── src/
│   ├── package.json
│   ├── firebase.json
│   └── ...
└── README.md
```

## Pasos para hacer el merge

```bash
# 1. Crear nuevo repo vacío
mkdir FlyPromocionesAdmin && cd FlyPromocionesAdmin
git init

# 2. Importar FlyBackend preservando historia
git remote add backend ../FlyBackend
git fetch backend
git merge --allow-unrelated-histories backend/main
git mv app alembic docker-compose.yml Dockerfile nginx requirements.txt scripts backend/
git commit -m "chore: mover FlyBackend a subdirectorio backend/"

# 3. Importar FlyMarketingDashboard preservando historia
git remote add dashboard ../FlyMarketingDashboard
git fetch dashboard
git merge --allow-unrelated-histories dashboard/main
git mv src public index.html package.json vite.config.ts firebase.json dashboard/
git commit -m "chore: mover FlyMarketingDashboard a subdirectorio dashboard/"

# 4. Mover los archivos .claude al root
cp backend/.claude/rules/api-conventions.md .claude/rules/
cp dashboard/.claude/rules/frontend-conventions.md .claude/rules/
# Crear CLAUDE.md unificado (combinar ambos)

# 5. Actualizar rutas en archivos de config
# - backend/alembic.ini: script_location = alembic
# - dashboard/.firebaserc: hosting apunta a dashboard/dist
# - .claude/commands/: actualizar paths
```

## Por qué vale la pena

- Un solo `CLAUDE.md` con el sistema completo → mejor contexto en cada sesión
- Cambios que afectan ambos (como la migración de user_id→fcm_token hoy) = 1 commit
- No hay que coordinar versiones entre repos
- PR/issues en un solo lugar
- Historia de git preservada de ambos proyectos
