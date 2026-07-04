import {
  coreServices,
  createBackendModule,
} from '@backstage/backend-plugin-api';
import { Entity } from '@backstage/catalog-model';
import {
  catalogProcessingExtensionPoint,
  EntityProvider,
  EntityProviderConnection,
} from '@backstage/plugin-catalog-node';

/**
 * BaselithCoreEntityProvider
 * 
 * Ingests BaselithCore plugins as Backstage Component entities by polling
 * the BaselithCore API /api/backstage/entities endpoint.
 */
export class BaselithCoreEntityProvider implements EntityProvider {
  private connection?: EntityProviderConnection;
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly logger: any;
  private readonly scheduler: any;

  constructor(options: {
    baseUrl: string;
    apiKey: string;
    logger: any;
    scheduler: any;
  }) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.logger = options.logger;
    this.scheduler = options.scheduler;
  }

  getProviderName(): string {
    return `baselith-core-provider`;
  }

  async connect(connection: EntityProviderConnection): Promise<void> {
    this.connection = connection;
    
    // Schedule periodic polling
    await this.scheduler.scheduleTask({
      id: 'baselith-core-sync',
      fn: async () => {
        await this.sync();
      },
      frequency: { minutes: 10 },
      timeout: { minutes: 1 },
    });
  }

  async sync() {
    if (!this.connection) {
      return;
    }

    this.logger.info(`Syncing BaselithCore plugins from ${this.baseUrl}...`);

    try {
      const response = await fetch(`${this.baseUrl}/api/backstage/entities`, {
        headers: {
          Authorization: `ApiKey ${this.apiKey}`,
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(
          `Failed to fetch BaselithCore entities: ${response.status} ${response.statusText}`,
        );
      }

      const data = (await response.json()) as {
        type: string;
        entities: Entity[];
      };

      if (data.type === 'full') {
        this.logger.info(`Discovered ${data.entities.length} plugins from BaselithCore.`);
        await this.connection.applyMutation({
          type: 'full',
          entities: data.entities.map(e => ({
            entity: e,
            locationKey: `baselith-core-provider`,
          })),
        });
      }
    } catch (error) {
      this.logger.error(`Failed to sync BaselithCore plugins: ${error}`);
    }
  }
}

/**
 * Registration module for the new Backstage backend system.
 */
export const baselithCoreModule = createBackendModule({
  pluginId: 'catalog',
  moduleId: 'baselith-core',
  register(env) {
    env.registerInit({
      deps: {
        // The processing extension point exposes addEntityProvider(); the
        // catalog *service* ref (query API) does not — depending on the latter
        // is what raised "catalog.addEntityProvider is not a function".
        catalog: catalogProcessingExtensionPoint,
        config: coreServices.rootConfig,
        logger: coreServices.logger,
        scheduler: coreServices.scheduler,
      },
      async init({ catalog, config, logger, scheduler }) {
        const baseUrl =
          config.getOptionalString('baselith.baseUrl') || 'http://localhost:8000';
        const apiKey =
          config.getOptionalString('baselith.apiKey') || '12345678';

        const provider = new BaselithCoreEntityProvider({
          baseUrl,
          apiKey,
          logger,
          scheduler,
        });

        catalog.addEntityProvider(provider);
      },
    });
  },
});
