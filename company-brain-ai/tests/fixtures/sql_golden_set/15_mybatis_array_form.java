package com.example.mapper;

import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Param;
import java.util.List;

public interface ReportMapper {

    @Select({
        "SELECT r.id, r.name, r.created_at, u.username AS created_by",
        "FROM reports r",
        "JOIN users u ON u.id = r.created_by_id",
        "WHERE r.workspace_id = #{workspaceId}",
        "ORDER BY r.created_at DESC"
    })
    List<ReportSummary> findRecentReports(@Param("workspaceId") String workspaceId);
}
