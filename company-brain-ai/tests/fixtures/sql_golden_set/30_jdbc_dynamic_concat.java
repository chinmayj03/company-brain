package com.example.dao;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;

public class SearchDao {

    /**
     * Dynamic query builder — demonstrates dynamic_concat tier detection.
     * The WHERE clause is built conditionally.
     */
    public List<String> searchNodes(Connection conn, String workspaceId,
                                     String nodeType, String nameFilter) throws Exception {
        String sql = "SELECT id, name FROM nodes WHERE workspace_id = ?";
        if (nodeType != null) {
            sql = sql + " AND node_type = ?";
        }
        if (nameFilter != null) {
            sql = sql + " AND lower(name) LIKE lower(?)";
        }
        sql = sql + " ORDER BY name LIMIT 100";

        PreparedStatement ps = conn.prepareStatement(sql);
        int idx = 1;
        ps.setString(idx++, workspaceId);
        if (nodeType != null) ps.setString(idx++, nodeType);
        if (nameFilter != null) ps.setString(idx++, "%" + nameFilter + "%");

        List<String> ids = new ArrayList<>();
        ResultSet rs = ps.executeQuery();
        while (rs.next()) ids.add(rs.getString("id"));
        return ids;
    }
}
