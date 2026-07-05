import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import apiDocsPlugin from '@backstage/plugin-api-docs/alpha';
import { navModule } from './modules/nav';

// api-docs ships BOTH a legacy root export and a new-system `/alpha` feature.
// `app.packages: all` discovery only picks up packages whose ROOT default is a
// new-system feature, so api-docs (root = legacy) is NOT auto-loaded — it must
// be registered explicitly, exactly like the catalog plugin above. Without it
// there is no "APIs" nav item, no OpenAPI definition card on API entities, and
// no "Provided APIs" card on plugin Components → the per-plugin API entities the
// framework exports are invisible in the portal.
export default createApp({
  features: [catalogPlugin, apiDocsPlugin, navModule],
});
