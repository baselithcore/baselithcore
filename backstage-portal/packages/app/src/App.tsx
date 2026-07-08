import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import apiDocsPlugin from '@backstage/plugin-api-docs/alpha';
import techdocsPlugin from '@backstage/plugin-techdocs/alpha';
import { navModule } from './modules/nav';

// api-docs and techdocs both ship BOTH a legacy root export and a new-system
// `/alpha` feature. `app.packages: all` discovery only picks up packages whose
// ROOT default is a new-system feature, so these (root = legacy) are NOT
// auto-loaded — they must be registered explicitly, exactly like the catalog
// plugin above. Without api-docs there is no "APIs" nav item / OpenAPI card;
// without techdocs the "Docs" nav item, the `/docs` reader, and the per-entity
// "Docs" tab do not render, so the TechDocs a plugin ships (a plugin dir with
// an mkdocs.yml → `backstage.io/techdocs-ref` on its Component) is invisible.
export default createApp({
  features: [catalogPlugin, apiDocsPlugin, techdocsPlugin, navModule],
});
