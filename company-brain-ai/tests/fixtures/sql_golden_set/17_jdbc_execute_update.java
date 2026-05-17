package com.example.dao;

import java.sql.Connection;
import java.sql.PreparedStatement;

public class OrderDao {

    public void updateOrderStatus(Connection conn, String orderId, String status) throws Exception {
        PreparedStatement ps = conn.prepareStatement(
            "UPDATE orders SET status = ?, updated_at = now() WHERE id = ?"
        );
        ps.setString(1, status);
        ps.setString(2, orderId);
        ps.executeUpdate();
        ps.close();
    }
}
