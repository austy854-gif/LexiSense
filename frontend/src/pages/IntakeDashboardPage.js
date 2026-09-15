import React, { useState, useEffect, useCallback } from 'react';
import { agenticApi, contractsAPI, workflowAPI } from '../api';
import {
  Card, Button, Table, Tag, Select, Input, Modal, Form, message,
  Space, Tooltip, Dropdown, Menu, Badge, Alert, Divider, Typography,
  DatePicker, Popconfirm, Avatar, Tabs, Steps, Timeline, Empty,
  Upload, Progress, Switch, Checkbox, Collapse, Descriptions,
  Image, Skeleton
} from 'antd';
import {
  MailOutlined, FileOutlined, SearchOutlined, FilterOutlined,
  EditOutlined, DeleteOutlined, EyeOutlined, DownloadOutlined,
  CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined,
  RightOutlined, LeftOutlined, SettingOutlined, ReloadOutlined,
  UserOutlined, TeamOutlined, FileTextOutlined, WarningOutlined,
  CheckOutlined, CloseOutlined, PaperClipOutlined, ArrowRightOutlined,
  SyncOutlined, DatabaseOutlined, RobotOutlined
} from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;
const { Panel } = Collapse;
const { Step } = Steps;

const IntakeDashboardPage = () => {
  const [loading, setLoading] = useState(false);
  const [intakes, setIntakes] = useState([]);
  const [selectedIntake, setSelectedIntake] = useState(null);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [processModalVisible, setProcessModalVisible] = useState(false);
  const [playbooks, setPlaybooks] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [filters, setFilters] = useState({
    status: '',
    dateRange: [],
    search: '',
  });
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });
  const [sortConfig, setSortConfig] = useState({ key: 'received_at', order: 'descend' });
  const [webhookConfig, setWebhookConfig] = useState(null);
  const [configModalVisible, setConfigModalVisible] = useState(false);

  const statusColors = {
    received: 'blue',
    processing: 'gold',
    classified: 'purple',
    routed: 'green',
    failed: 'red',
    manual_review: 'orange',
  };

  const statusLabels = {
    received: 'Received',
    processing: 'Processing',
    classified: 'Classified',
    routed: 'Routed',
    failed: 'Failed',
    manual_review: 'Manual Review',
  };

  const intakeStatusSteps = [
    { key: 'received', title: 'Received', description: 'Email received via Resend webhook' },
    { key: 'processing', title: 'Processing', description: 'Attachments extracted & stored' },
    { key: 'classified', title: 'Classified', description: 'AI classified contract type & counterparty' },
    { key: 'routed', title: 'Routed', description: 'Assigned to team & playbook selected' },
    { key: 'contract_created', title: 'Contract Created', description: 'Contract record created in system' },
  ];

  const fetchIntakes = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        status: filters.status,
        limit: pagination.pageSize,
        skip: (pagination.current - 1) * pagination.pageSize,
      };
      
      if (filters.dateRange && filters.dateRange.length === 2) {
        params.received_after = filters.dateRange[0].toISOString();
        params.received_before = filters.dateRange[1].toISOString();
      }
      
      if (filters.search) {
        params.search = filters.search;
      }
      
      const res = await agenticApi.listIntakes(params);
      const data = res.data || [];
      setIntakes(data);
      setPagination(prev => ({ ...prev, total: res.total || data.length }));
    } catch (e) {
      message.error('Failed to load intakes');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [filters, pagination.current, pagination.pageSize]);

  const fetchPlaybooks = useCallback(async () => {
    try {
      const res = await agenticApi.listPlaybooks();
      setPlaybooks(res.data || []);
    } catch (e) {
      console.error('Failed to load playbooks:', e);
    }
  }, []);

  const fetchTeamMembers = useCallback(async () => {
    try {
      const res = await agenticApi.listIntakes({ limit: 1 });
      // Use team API if available, fallback to mock
      const teamRes = await fetch('/api/team/members', {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });
      if (teamRes.ok) {
        const data = await teamRes.json();
        setTeamMembers(data.data || []);
      }
    } catch (e) {
      console.error('Failed to load team members:', e);
    }
  }, []);

  const fetchWebhookConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/agentic/intake/config', {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });
      if (res.ok) {
        const data = await res.json();
        setWebhookConfig(data);
      }
    } catch (e) {
      console.error('Failed to load webhook config:', e);
    }
  }, []);

  useEffect(() => {
    fetchIntakes();
    fetchPlaybooks();
    fetchTeamMembers();
    fetchWebhookConfig();
  }, [fetchIntakes, fetchPlaybooks, fetchTeamMembers, fetchWebhookConfig]);

  const handleTableChange = (pagination, filters, sorter) => {
    if (sorter.field) {
      setSortConfig({ key: sorter.field, order: sorter.order });
    }
    setPagination(prev => ({ ...prev, current: pagination.current, pageSize: pagination.pageSize }));
  };

  const handleSearch = (value) => {
    setFilters(prev => ({ ...prev, search: value }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleStatusFilter = (value) => {
    setFilters(prev => ({ ...prev, status: value }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleDateRangeChange = (dates) => {
    setFilters(prev => ({ ...prev, dateRange: dates }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleViewIntake = (intake) => {
    setSelectedIntake(intake);
    setDetailModalVisible(true);
  };

  const handleProcessIntake = (intake) => {
    setSelectedIntake(intake);
    setProcessModalVisible(true);
  };

  const handleCreateContract = async (intakeId) => {
    try {
      // Process intake with default options to create contract
      await agenticApi.processIntake(intakeId, { 
        assign_to: 'auto',
        playbook_id: 'auto'
      });
      message.success('Contract creation initiated');
      fetchIntakes();
    } catch (e) {
      message.error('Failed to create contract');
    }
  };

  const handleProcessSubmit = async (values) => {
    try {
      await agenticApi.processIntake(selectedIntake.id, values);
      message.success('Intake processed successfully');
      setProcessModalVisible(false);
      fetchIntakes();
    } catch (e) {
      message.error('Failed to process intake');
    }
  };

  const handleRetryFailed = async () => {
    try {
      await agenticApi.retryFailedIntakes();
      message.success('Failed intakes retry queued');
      fetchIntakes();
    } catch (e) {
      message.error('Failed to retry');
    }
  };

  const getCurrentStepIndex = (status) => {
    const statusOrder = ['received', 'processing', 'classified', 'routed'];
    return statusOrder.indexOf(status);
  };

  const renderStatusBadge = (status) => (
    <Tag color={statusColors[status] || 'default'}>
      {statusLabels[status] || status}
    </Tag>
  );

  const renderAttachments = (attachments) => {
    if (!attachments || attachments.length === 0) return '-';
    return (
      <Space direction="vertical" size={2}>
        {attachments.map((att, idx) => (
          <Space key={idx} size={4}>
            <PaperClipOutlined />
            <Text ellipsis={{ rows: 1 }}>{att.filename}</Text>
            <Badge count={Math.round(att.size / 1024)} text={`${Math.round(att.size / 1024)} KB`} />
          </Space>
        ))}
      </Space>
    );
  };

  const intakeColumns = [
    { 
      title: 'Sender', 
      dataIndex: 'source_email', 
      key: 'source_email', 
      width: 200,
      sorter: true,
      render: (_, record) => (
        <Space>
          <Avatar size={28} icon={<MailOutlined />} />
          <Text strong>{record.source_email}</Text>
        </Space>
      )
    },
    { 
      title: 'Subject', 
      dataIndex: 'source_metadata.subject', 
      key: 'subject', 
      width: 250,
      sorter: true,
      render: (v) => <Text ellipsis={{ rows: 1 }}>{v || 'No subject'}</Text>
    },
    { 
      title: 'Status', 
      dataIndex: 'status', 
      key: 'status', 
      width: 130,
      sorter: true,
      filters: Object.entries(statusLabels).map(([value, label]) => ({ text: label, value })),
      onFilter: (value, record) => record.status === value,
      render: renderStatusBadge
    },
    { 
      title: 'Classified Type', 
      dataIndex: 'classified_type', 
      key: 'classified_type', 
      width: 150,
      sorter: true,
      render: (v) => v ? <Tag color="geekblue">{v}</Tag> : <Tag color="default">—</Tag>
    },
    { 
      title: 'Counterparty', 
      dataIndex: 'classified_counterparty', 
      key: 'classified_counterparty', 
      width: 150,
      sorter: true,
      render: (v) => v ? <Text ellipsis>{{v}}</Text> : <Text type="secondary">—</Text>
    },
    { 
      title: 'Confidence', 
      dataIndex: 'confidence', 
      key: 'confidence', 
      width: 100,
      sorter: true,
      render: (v) => (
        <Space size={4}>
          <Progress type="circle" width={40} strokeWidth={4} percent={Math.round(v * 100)} />
          <Text>{Math.round(v * 100)}%</Text>
        </Space>
      )
    },
    { 
      title: 'Attachments', 
      dataIndex: 'attachments', 
      key: 'attachments', 
      width: 120,
      render: renderAttachments
    },
    { 
      title: 'Assigned To', 
      dataIndex: 'assigned_to', 
      key: 'assigned_to', 
      width: 120,
      render: (v) => v ? (
        <Avatar size={28} src={null}>{v.charAt(0).toUpperCase()}</Avatar>
      ) : <Tag color="default">Unassigned</Tag>
    },
    { 
      title: 'Playbook', 
      dataIndex: 'playbook_id', 
      key: 'playbook_id', 
      width: 150,
      render: (v, record) => {
        if (!v) return <Tag color="default">—</Tag>;
        const pb = playbooks.find(p => p.id === v);
        return pb ? <Tag color="green">{pb.name}</Tag> : <Tag>{v}</Tag>;
      }
    },
    { 
      title: 'Created Contract', 
      dataIndex: 'created_contract_id', 
      key: 'created_contract_id', 
      width: 150,
      render: (v) => v ? (
        <Button type="link" onClick={() => window.location.href = `/contracts/${v}`}>
          <FileTextOutlined /> View Contract
        </Button>
      ) : <Tag color="default">—</Tag>
    },
    { 
      title: 'Received', 
      dataIndex: 'received_at', 
      key: 'received_at', 
      width: 160,
      sorter: true,
      render: (v) => v ? new Date(v).toLocaleString() : '-'
    },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 200,
      fixed: 'right',
      render: (_, record) => (
        <Space>
          <Button type="link" icon={<EyeOutlined />} onClick={() => handleViewIntake(record)}>View</Button>
          {record.status !== 'routed' && record.status !== 'failed' && (
            <Button type="link" icon={<RightOutlined />} onClick={() => handleProcessIntake(record)}>Process</Button>
          )}
          {record.status === 'routed' && !record.created_contract_id && (
            <Button type="link" icon={<DatabaseOutlined />} onClick={() => handleCreateContract(record.id)}>Create Contract</Button>
          )}
          {record.status === 'failed' && (
            <Button type="link" danger icon={<SyncOutlined />} onClick={() => handleProcessIntake(record)}>Retry</Button>
          )}
        </Space>
      )
    },
  ];

  // Detail Modal Content
  const renderDetailModal = () => {
    if (!selectedIntake) return null;
    
    const intake = selectedIntake;
    const currentStep = getCurrentStepIndex(intake.status);
    
    return (
      <Modal
        title={`Intake Details: ${intake.source_metadata?.subject || 'No Subject'}`}
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        width={900}
        footer={null}
        centered
      >
        <div style={{ padding: 16 }}>
          {/* Status Progress */}
          <Card style={{ marginBottom: 16 }}>
            <Steps current={Math.max(0, currentStep)} size="small" direction="vertical" style={{ width: '100%' }}>
              {intakeStatusSteps.map((step, idx) => (
                <Step key={step.key} title={step.title} description={step.description} />
              ))}
              <Step key="contract_created" title="Contract Created" description="Contract record created & workflow started" />
            </Steps>
          </Card>

          <Tabs defaultActiveKey="overview" items={[
            {
              key: 'overview',
              tab: 'Overview',
              children: (
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                  <Descriptions column={2} title="Basic Information">
                    <Descriptions.Item label="Intake ID">{intake.id}</Descriptions.Item>
                    <Descriptions.Item label="Channel"><Tag color="blue">{intake.channel}</Tag></Descriptions.Item>
                    <Descriptions.Item label="Sender">{intake.source_email}</Descriptions.Item>
                    <Descriptions.Item label="Subject">{intake.source_metadata?.subject || 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="Received">{intake.received_at ? new Date(intake.received_at).toLocaleString() : 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="Processing Started">{intake.processing_started_at ? new Date(intake.processing_started_at).toLocaleString() : 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="Processing Completed">{intake.processing_completed_at ? new Date(intake.processing_completed_at).toLocaleString() : 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="Error">{intake.error_message || 'None'}</Descriptions.Item>
                  </Descriptions>
                  
                  <Divider>Classification Results</Divider>
                  <Descriptions column={2} title="AI Classification">
                    <Descriptions.Item label="Contract Type">{intake.classified_type || 'Not classified'}</Descriptions.Item>
                    <Descriptions.Item label="Counterparty">{intake.classified_counterparty || 'Not identified'}</Descriptions.Item>
                    <Descriptions.Item label="Confidence">
                      <Progress type="circle" width={50} strokeWidth={4} percent={Math.round((intake.confidence || 0) * 100)} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Reasoning">{intake.classification_reasoning || 'N/A'}</Descriptions.Item>
                  </Descriptions>
                  
                  <Divider>Routing Information</Divider>
                  <Descriptions column={2} title="Assignment & Playbook">
                    <Descriptions.Item label="Assigned To">{intake.assigned_to || 'Unassigned'}</Descriptions.Item>
                    <Descriptions.Item label="Team">{intake.assigned_team || 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="Playbook">
                      {intake.playbook_id ? (
                        <Space>
                          {(() => {
                            const pb = playbooks.find(p => p.id === intake.playbook_id);
                            return pb ? <Tag color="green">{pb.name}</Tag> : <Tag>{intake.playbook_id}</Tag>;
                          })()}
                        </Space>
                      ) : <Tag color="default">No playbook assigned</Tag>}
                    </Descriptions.Item>
                  </Descriptions>
                  
                  <Divider>Attachments</Divider>
                  <div>
                    {intake.attachments && intake.attachments.length > 0 ? (
                      intake.attachments.map((att, idx) => (
                        <Card key={idx} style={{ marginBottom: 8, width: '100%' }}>
                          <Space>
                            <FileOutlined />
                            <Text strong>{att.filename}</Text>
                            <Badge count={Math.round(att.size / 1024)} text={`${Math.round(att.size / 1024)} KB`} />
                            <Tag>{att.mime_type}</Tag>
                            <Button type="link" icon={<DownloadOutlined />}>Download</Button>
                          </Space>
                        </Card>
                      ))
                    ) : (
                      <Empty description="No attachments" />
                    )}
                  </div>
                </Space>
              )
            },
            {
              key: 'source',
              tab: 'Raw Source Data',
              children: (
                <Card>
                  <pre style={{ 
                    maxHeight: 400, 
                    overflow: 'auto', 
                    background: '#f5f5f5', 
                    padding: 16, 
                    borderRadius: 8,
                    fontSize: 12 
                  }}>
                    {JSON.stringify(intake.source_metadata, null, 2)}
                  </pre>
                </Card>
              )
            },
            {
              key: 'history',
              tab: 'Processing History',
              children: (
                <Timeline>
                  <Timeline.Item label={intake.received_at ? new Date(intake.received_at).toLocaleString() : ''}>
                    <Text strong>Received</Text>
                    <Paragraph>Email received via {intake.channel} channel from {intake.source_email}</Paragraph>
                  </Timeline.Item>
                  {intake.processing_started_at && (
                    <Timeline.Item label={new Date(intake.processing_started_at).toLocaleString()}>
                      <Text strong>Processing Started</Text>
                      <Paragraph>Attachments extracted and stored</Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.classified_type && (
                    <Timeline.Item label={intake.processing_completed_at ? new Date(intake.processing_completed_at).toLocaleString() : ''}>
                      <Text strong>Classified</Text>
                      <Paragraph>Type: {intake.classified_type} | Counterparty: {intake.classified_counterparty || 'Unknown'} | Confidence: {Math.round((intake.confidence || 0) * 100)}%</Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.playbook_id && (
                    <Timeline.Item label="">
                      <Text strong>Playbook Assigned</Text>
                      <Paragraph>{(() => { const pb = playbooks.find(p => p.id === intake.playbook_id); return pb ? pb.name : intake.playbook_id; })()}</Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.assigned_to && (
                    <Timeline.Item label="">
                      <Text strong>Assigned</Text>
                      <Paragraph>Assigned to user: {intake.assigned_to}</Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.status === 'routed' && (
                    <Timeline.Item label={intake.processing_completed_at ? new Date(intake.processing_completed_at).toLocaleString() : ''}>
                      <Text strong>Routed</Text>
                      <Paragraph>Intake fully processed and routed for review</Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.created_contract_id && (
                    <Timeline.Item label="">
                      <Text strong>Contract Created</Text>
                      <Paragraph>
                        Contract ID: {intake.created_contract_id}
                        <Button type="link" size="small" style={{ marginLeft: 8 }} onClick={() => window.location.href = `/contracts/${intake.created_contract_id}`}>
                          View Contract
                        </Button>
                      </Paragraph>
                    </Timeline.Item>
                  )}
                  {intake.error_message && (
                    <Timeline.Item label="" color="red">
                      <Text strong>Error</Text>
                      <Paragraph>{intake.error_message}</Paragraph>
                    </Timeline.Item>
                  )}
                </Timeline>
              )
            }
          ]} />
        </div>
      </Modal>
    );
  };

  // Process Modal Content
  const renderProcessModal = () => {
    if (!selectedIntake) return null;
    
    const [form] = Form.useForm();
    
    useEffect(() => {
      form.setFieldsValue({
        assign_to: selectedIntake.assigned_to || '',
        playbook_id: selectedIntake.playbook_id || '',
      });
    }, [selectedIntake, form]);

    return (
      <Modal
        title={`Process Intake: ${selectedIntake.source_metadata?.subject || 'No Subject'}`}
        open={processModalVisible}
        onCancel={() => setProcessModalVisible(false)}
        width={600}
        footer={null}
        centered
      >
        <Form form={form} layout="vertical" onFinish={handleProcessSubmit}>
          <Alert message="Processing will classify the contract, assign a playbook, and route to the appropriate team member." type="info" showIcon />
          
          <Form.Item name="assign_to" label="Assign To" rules={[{ required: false }]}>
            <Select
              placeholder="Select team member (or leave empty for auto-assign)"
              allowClear
              showSearch
              options={teamMembers.map(m => ({ value: m.id, label: `${m.firstName || ''} ${m.lastName || ''} (${m.email})` }))}
            />
          </Form.Item>
          
          <Form.Item name="playbook_id" label="Playbook" rules={[{ required: false }]}>
            <Select
              placeholder="Select playbook (or leave empty for auto-select)"
              allowClear
              showSearch
              options={playbooks.map(p => ({ value: p.id, label: `${p.name} v${p.version} (${p.contract_types?.join(', ') || 'All'})` }))}
            />
          </Form.Item>
          
          <Form.Item name="auto_create_contract" label="Auto-create contract record" valuePropName="checked">
            <Checkbox>Create contract record after processing</Checkbox>
          </Form.Item>
          
          <Form.Item name="trigger_risk_assessment" label="Trigger risk assessment" valuePropName="checked">
            <Checkbox>Run automated risk scoring after contract creation</Checkbox>
          </Form.Item>
          
          <Form.Item name="trigger_obligation_extraction" label="Extract obligations" valuePropName="checked">
            <Checkbox>Extract post-signature obligations (for signed contracts)</Checkbox>
          </Form.Item>
          
          <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
            <Button htmlType="submit" type="primary" icon={<RightOutlined />}>Process Intake</Button>
            <Button onClick={() => setProcessModalVisible(false)}>Cancel</Button>
          </Space>
        </Form>
      </Modal>
    );
  };

  // Webhook Config Modal
  const renderConfigModal = () => (
    <Modal
      title="Resend Webhook Configuration"
      open={configModalVisible}
      onCancel={() => setConfigModalVisible(false)}
      width={700}
      footer={null}
    >
      <Card>
        <Alert message="Configure your Resend webhook to enable zero-training email intake." type="info" showIcon />
        
        <Descriptions column={1} title="Webhook Endpoint">
          <Descriptions.Item label="URL">
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Code style={{ background: '#f5f5f5', padding: '4px 8px', borderRadius: 4, fontSize: 13 }}>
                {window.location.origin}/api/v1/agentic/intake/webhook/resend
              </Code>
              <Button icon={<CopyOutlined />} onClick={() => {
                navigator.clipboard.writeText(`${window.location.origin}/api/v1/agentic/intake/webhook/resend`);
                message.success('Copied to clipboard');
              }}>Copy</Button>
            </div>
          </Descriptions.Item>
          <Descriptions.Item label="Method">POST</Descriptions.Item>
          <Descriptions.Item label="Content-Type">application/json</Descriptions.Item>
          <Descriptions.Item label="Secret Header">X-Resend-Signature (HMAC-SHA256)</Descriptions.Item>
        </Descriptions>
        
        <Divider>Expected Payload Format</Divider>
        <pre style={{ 
          maxHeight: 300, 
          overflow: 'auto', 
          background: '#f5f5f5', 
          padding: 16, 
          borderRadius: 8,
          fontSize: 12 
        }}>
{`{
  "type": "email.received",
  "created_at": "2024-01-15T10:30:00.000Z",
  "data": {
    "id": "msg_abc123",
    "from": "sender@example.com",
    "to": ["contracts@yourcompany.lexisense.app"],
    "subject": "New NDA for Review",
    "text": "Please find attached...",
    "html": "<p>Please find attached...</p>",
    "attachments": [
      {
        "filename": "NDA.pdf",
        "content_type": "application/pdf",
        "content": "base64-encoded-content..."
      }
    ]
  }
}`}
        </pre>
        
        <Divider>Setup Instructions</Divider>
        <ol style={{ paddingLeft: 20, lineHeight: 1.8 }}>
          <li>Go to <a href="https://resend.com/domains" target="_blank" rel="noopener noreferrer">Resend Dashboard → Domains</a></li>
          <li>Select your domain and click "Webhooks"</li>
          <li>Add new webhook with the URL above</li>
          <li>Select "email.received" event</li>
          <li>Set webhook secret in environment variable: <code>RESEND_WEBHOOK_SECRET</code></li>
          <li>Configure intake email address (e.g., <code>contracts@yourcompany.lexisense.app</code>)</li>
        </ol>
        
        <Divider>Test Webhook</Divider>
        <Button 
          type="primary" 
          icon={<SettingOutlined />} 
          onClick={() => {
            // Trigger test webhook
            fetch('/api/agentic/intake/test-webhook', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
              }
            }).then(r => r.json()).then(d => {
              if (d.success) message.success('Test webhook sent');
              else message.error('Failed: ' + d.error);
            });
          }}
        >
          Send Test Webhook
        </Button>
      </Card>
    </Modal>
  );

  return (
    <div className="page-container">
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 16 }}>
          <div>
            <h1>Business Intake Dashboard</h1>
            <p>Zero-training contract intake via email - powered by Resend webhooks & AI classification</p>
          </div>
          <Space>
            <Button icon={<SettingOutlined />} onClick={() => setConfigModalVisible(true)}>
              Webhook Config
            </Button>
            <Button icon={<SyncOutlined />} onClick={handleRetryFailed}>
              Retry Failed
            </Button>
            <Button type="primary" icon={<ReloadOutlined />} onClick={fetchIntakes} loading={loading}>
              Refresh
            </Button>
          </Space>
        </div>
      </div>

      {/* Stats Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 24 }}>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 14 }}>Total Intakes</Text>
              <Title level={3} style={{ margin: 0 }}>{intakes.length}</Title>
            </div>
            <Avatar size={48} style={{ background: '#e6f7ff' }}>
              <MailOutlined style={{ fontSize: 20 }} />
            </Avatar>
          </div>
        </Card>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 14 }}>Pending Processing</Text>
              <Title level={3} style={{ margin: 0 }}>
                {intakes.filter(i => ['received', 'processing'].includes(i.status)).length}
              </Title>
            </div>
            <Avatar size={48} style={{ background: '#fff7e6' }}>
              <ClockCircleOutlined style={{ fontSize: 20 }} />
            </Avatar>
          </div>
        </Card>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 14 }}>Classified & Routed</Text>
              <Title level={3} style={{ margin: 0 }}>
                {intakes.filter(i => ['classified', 'routed'].includes(i.status)).length}
              </Title>
            </div>
            <Avatar size={48} style={{ background: '#f6ffed' }}>
              <CheckCircleOutlined style={{ fontSize: 20 }} />
            </Avatar>
          </div>
        </Card>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 14 }}>Contracts Created</Text>
              <Title level={3} style={{ margin: 0 }}>
                {intakes.filter(i => i.created_contract_id).length}
              </Title>
            </div>
            <Avatar size={48} style={{ background: '#f9f0ff' }}>
              <DatabaseOutlined style={{ fontSize: 20 }} />
            </Avatar>
          </div>
        </Card>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 14 }}>Failed</Text>
              <Title level={3} style={{ margin: 0, color: '#ff4d4f' }}>
                {intakes.filter(i => i.status === 'failed').length}
              </Title>
            </div>
            <Avatar size={48} style={{ background: '#fff1f0' }}>
              <ExclamationCircleOutlined style={{ fontSize: 20 }} />
            </Avatar>
          </div>
        </Card>
      </div>

      {/* Filters */}
      <Card style={{ marginBottom: 16 }}>
        <Form layout="inline" onFinish={(values) => {
          setFilters(prev => ({ ...prev, ...values }));
          setPagination(prev => ({ ...prev, current: 1 }));
        }}>
          <Form.Item name="status" label="Status">
            <Select placeholder="All Statuses" allowClear style={{ width: 160 }}>
              {Object.entries(statusLabels).map(([value, label]) => (
                <Option key={value} value={value}>{label}</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="dateRange" label="Received Date">
            <RangePicker 
              style={{ width: 280 }} 
              allowClear
              format="YYYY-MM-DD"
            />
          </Form.Item>
          <Form.Item name="search" label="Search">
            <Input.Search
              placeholder="Search sender, subject, counterparty..."
              enterButton
              style={{ width: 280 }}
              onSearch={handleSearch}
            />
          </Form.Item>
        </Form>
      </Card>

      {/* Intake Table */}
      <Card>
        <Table
          columns={intakeColumns}
          dataSource={intakes}
          loading={loading}
          rowKey="id"
          pagination={pagination}
          onChange={handleTableChange}
          defaultSortOrder="descend"
          defaultSortedColumns={[{ columnKey: 'received_at', order: 'descend' }]}
          expandable={{
            expandedRowRender: (record) => (
              <div style={{ padding: '16px 24px', background: '#fafafa' }}>
                <Descriptions column={4} title="Quick Details">
                  <Descriptions.Item label="Sender">{record.source_email}</Descriptions.Item>
                  <Descriptions.Item label="Subject">{record.source_metadata?.subject || 'N/A'}</Descriptions.Item>
                  <Descriptions.Item label="Type">{record.classified_type || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Counterparty">{record.classified_counterparty || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Confidence">{Math.round((record.confidence || 0) * 100)}%</Descriptions.Item>
                  <Descriptions.Item label="Attachments">{record.attachments?.length || 0}</Descriptions.Item>
                  <Descriptions.Item label="Assigned To">{record.assigned_to || 'Unassigned'}</Descriptions.Item>
                  <Descriptions.Item label="Playbook">
                    {record.playbook_id ? (
                      <Tag color="green">
                        {(() => { const pb = playbooks.find(p => p.id === record.playbook_id); return pb ? pb.name : record.playbook_id; })()}
                      </Tag>
                    ) : '—'}
                  </Descriptions.Item>
                </Descriptions>
              </div>
            ),
            rowExpandable: (record) => !!record.attachments?.length,
          }}
          scroll={{ x: 1400 }}
        />
      </Card>

      {renderDetailModal()}
      {renderProcessModal()}
      {renderConfigModal()}
    </div>
  );
};

export default IntakeDashboardPage;