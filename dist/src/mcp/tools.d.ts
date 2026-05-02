/**
 * Debugger MCP Tool Definitions
 *
 * 8 tools for debugging memory: search, store, detail, status, list, patterns, outcome, read_logs.
 * Each calls existing programmatic APIs from storage.ts and retrieval.ts.
 * Responses are formatted as concise text for LLM consumption.
 */
export declare const TOOLS: ({
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            symptom: {
                type: string;
                description: string;
            };
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            limit?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required: string[];
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            symptom: {
                type: string;
                description: string;
            };
            root_cause: {
                type: string;
                description: string;
            };
            category: {
                type: string;
                description: string;
            };
            fix: {
                type: string;
                description: string;
            };
            tags: {
                type: string;
                items: {
                    type: string;
                };
                description: string;
            };
            files_changed: {
                type: string;
                items: {
                    type: string;
                };
                description: string;
            };
            file: {
                type: string;
                description: string;
            };
            id?: undefined;
            limit?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required: string[];
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            id: {
                type: string;
                description: string;
            };
            symptom?: undefined;
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            limit?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required: string[];
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            symptom?: undefined;
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            limit?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required?: undefined;
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            limit: {
                type: string;
                description: string;
            };
            category: {
                type: string;
                description: string;
            };
            symptom?: undefined;
            root_cause?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required?: undefined;
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            limit: {
                type: string;
                description: string;
            };
            symptom?: undefined;
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required?: undefined;
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            incident_id: {
                type: string;
                description: string;
            };
            result: {
                type: string;
                enum: string[];
                description: string;
            };
            notes: {
                type: string;
                description: string;
            };
            symptom?: undefined;
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            limit?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
        };
        required: string[];
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
} | {
    name: string;
    description: string;
    inputSchema: {
        type: "object";
        properties: {
            source: {
                type: string;
                enum: string[];
                description: string;
            };
            path: {
                type: string;
                description: string;
            };
            since: {
                type: string;
                description: string;
            };
            until: {
                type: string;
                description: string;
            };
            level: {
                type: string;
                enum: string[];
                description: string;
            };
            keyword: {
                type: string;
                description: string;
            };
            limit: {
                type: string;
                description: string;
            };
            symptom?: undefined;
            root_cause?: undefined;
            category?: undefined;
            fix?: undefined;
            tags?: undefined;
            files_changed?: undefined;
            file?: undefined;
            id?: undefined;
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
        };
        required: string[];
    };
    annotations: {
        title: string;
        readOnlyHint: boolean;
        destructiveHint: boolean;
        idempotentHint: boolean;
        openWorldHint: boolean;
    };
})[];
export declare function handleToolCall(name: string, args: Record<string, unknown>): Promise<{
    content: Array<{
        type: string;
        text: string;
    }>;
    isError?: boolean;
}>;
//# sourceMappingURL=tools.d.ts.map