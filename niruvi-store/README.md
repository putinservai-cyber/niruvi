# Niruvi Store

A Linux application marketplace that integrates with the Niruvi AppImage manager.

## Vision

Niruvi Store provides application discovery, metadata, and download information that integrates with the [Niruvi](https://github.com/putinservai-cyber/niruvi) desktop application for installation and management.

## Philosophy

- **Separate concerns**: Niruvi handles installation, verification, and local management; Niruvi Store handles discovery and metadata
- **Beginner-friendly**: Simple technology stack, no complex backend required for MVP
- **Security-first**: SHA-256 verification, publisher information, license display
- **Cost-effective**: GitHub Pages / GitHub Releases for hosting, static JSON catalog
- **Incremental**: Build gradually, each milestone works before the next

## MVP Features

- Static application catalog (JSON)
- Search and filtering by category/architecture/license
- Application detail pages with full metadata
- "Install with Niruvi" protocol links (`niruvi://install/...`)
- Manual curation of initial applications
- Responsive design

## Technology Stack (MVP)

- **Frontend**: React + TypeScript + Vite
- **Styling**: Tailwind CSS
- **Catalog**: Static JSON files
- **Hosting**: GitHub Pages (free)

## Getting Started

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```

## License

GPL-3.0-only