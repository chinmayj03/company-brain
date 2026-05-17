package com.example.dao;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;

public class ReportDao {

    public List<String> getActiveUserIds(Connection conn, String workspaceId) throws Exception {
        List<String> ids = new ArrayList<>();
        PreparedStatement ps = conn.prepareStatement(
            "SELECT id FROM users WHERE workspace_id = ? AND status = 'active' ORDER BY created_at DESC"
        );
        ps.setString(1, workspaceId);
        ResultSet rs = ps.executeQuery();
        while (rs.next()) {
            ids.add(rs.getString("id"));
        }
        return ids;
    }
}
