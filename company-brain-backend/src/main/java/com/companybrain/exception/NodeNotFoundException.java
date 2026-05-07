package com.companybrain.exception;

import java.util.UUID;

public class NodeNotFoundException extends RuntimeException {

    public NodeNotFoundException(UUID nodeId) {
        super("Node not found: " + nodeId);
    }
}
