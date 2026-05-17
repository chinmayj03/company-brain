package com.example.mapper;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;

public interface AuditMapper {

    @Insert("INSERT INTO audit_log (id, workspace_id, action, entity_id, created_at) " +
            "VALUES (#{id}, #{workspaceId}, #{action}, #{entityId}, now())")
    @Options(useGeneratedKeys = true, keyProperty = "id")
    int insertAuditEntry(@Param("id") String id,
                         @Param("workspaceId") String workspaceId,
                         @Param("action") String action,
                         @Param("entityId") String entityId);
}
