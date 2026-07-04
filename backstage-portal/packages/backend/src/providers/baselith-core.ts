import {
  coreServices,
  createBackendModule,
  LoggerService,
  SchedulerService,
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
 * Ingests the BaselithCore plugin ecosystem as a complete Backstage entity
 * graph (Domain + System + Groups + Resources + Components + APIs) by polling
 * the framework's /api/backstage/entities endpoint.
 *
 * The endpoint supports conditional requests: we replay the last ETag via
 * If-None-Match, and an unchanged catalog costs a 304 with no body (the
 * previously applied mutation stays in place) instead of a full re-ingestion.
 */
export class BaselithCoreEntityProvider implements EntityProvider {
  private connection?: EntityProviderConnection;
  private lastEtag?: string;
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly logger: LoggerService;
  private readonly scheduler: SchedulerService;
  private readonly frequencyMinutes: number;
  private readonly timeoutMinutes: number;

  constructor(options: {
    baseUrl: string;
    apiKey: string;
    logger: LoggerService;
    scheduler: SchedulerService;
    frequencyMinutes?: number;
    timeoutMinutes?: number;
  }) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.logger = options.logger;
    this.scheduler = options.scheduler;
    this.frequencyMinutes = options.frequencyMinutes ?? 10;
    this.timeoutMinutes = options.timeoutMinutes ?? 1;
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
      frequency: { minutes: this.frequencyMinutes },
      timeout: { minutes: this.timeoutMinutes },
    });
  }

  async sync() {
    if (!this.connection) {
      return;
    }

    this.logger.info(`Syncing BaselithCore plugins from ${this.baseUrl}...`);

    try {
      const headers: Record<string, string> = {
        Authorization: `ApiKey ${this.apiKey}`,
        Accept: 'application/json',
      };
      if (this.lastEtag) {
        headers['If-None-Match'] = this.lastEtag;
      }

      const response = await fetch(`${this.baseUrl}/api/backstage/entities`, {
        headers,
      });

      if (response.status === 304) {
        this.logger.debug(
          'BaselithCore catalog unchanged (304) — keeping current entities.',
        );
        return;
      }

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
        this.logger.info(
          `Discovered ${data.entities.length} catalog entities from BaselithCore.`,
        );
        await this.connection.applyMutation({
          type: 'full',
          entities: data.entities.map(e => ({
            entity: e,
            locationKey: `baselith-core-provider`,
          })),
        });
        // Only remember the ETag once the mutation is applied, so a failed
        // apply is retried with a full fetch on the next tick.
        this.lastEtag = response.headers.get('etag') ?? undefined;
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
        const frequencyMinutes = config.getOptionalNumber(
          'baselith.schedule.frequencyMinutes',
        );
        const timeoutMinutes = config.getOptionalNumber(
          'baselith.schedule.timeoutMinutes',
        );

        const provider = new BaselithCoreEntityProvider({
          baseUrl,
          apiKey,
          logger,
          scheduler,
          frequencyMinutes,
          timeoutMinutes,
        });

        catalog.addEntityProvider(provider);
      },
    });
  },
});
