# Niruvi Store

## Project Structure

```
niruvi-store/
├── apps/                    # Application metadata and assets
│   └── example-app/
│       ├── metadata.json
│       ├── icon.png
│       └── screenshots/
│
├── catalog/                 # Catalog data
│   └── catalog.json
│
├── src/                     # Source code
│   ├── components/          # React components
│   ├── pages/               # Page components
│   ├── services/            # Data services
│   ├── data/                # Local data
│   └── types/               # TypeScript types
│
├── public/                  # Static assets
│
├── docs/                    # Documentation
│
├── tests/                   # Test files
│
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
├── README.md
└── LICENSE
```

## Initial Setup

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```