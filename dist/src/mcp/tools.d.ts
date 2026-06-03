/**
 * Debugger MCP Tool Definitions
 *
 * 11 tools: 8 debugging-memory tools plus 3 build-loop-memory context tools.
 * Debugging tools call storage.ts/retrieval.ts; build-loop-memory tools call the host-neutral CLI.
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir?: undefined;
            project?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir: {
                type: string;
                description: string;
            };
            project: {
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
            incident_id?: undefined;
            result?: undefined;
            notes?: undefined;
            source?: undefined;
            path?: undefined;
            since?: undefined;
            until?: undefined;
            level?: undefined;
            keyword?: undefined;
            query?: undefined;
            mode?: undefined;
            write?: undefined;
            max_chars?: undefined;
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
            workdir: {
                type: string;
                description: string;
            };
            query: {
                type: string;
                description: string;
            };
            project: {
                type: string;
                description: string;
            };
            mode: {
                type: string;
                enum: string[];
                description: string;
            };
            limit: {
                type: string;
                description: string;
            };
            write: {
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
            max_chars?: undefined;
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
            id: {
                type: string;
                description: string;
            };
            workdir: {
                type: string;
                description: string;
            };
            project: {
                type: string;
                description: string;
            };
            max_chars: {
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
            query?: undefined;
            mode?: undefined;
            write?: undefined;
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