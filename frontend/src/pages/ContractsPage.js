import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { contractsAPI, exportAPI } from '../api';
import { useAuth } from '../contexts/AuthContext';
import { usePagination } from '../hooks/usePagination';
import { PaginationBar } from '../components/PaginationBar';
import { EmptyState } from '../components/EmptyState';
import {
  NoContractsIllustration,
  NoSearchResultsIllustration,
  NothingExpiringIllustration,
  SomethingWentWrongIllustration,
  UploadFailedIllustration,
} from '../components/illustrations/Illustrations';
import { canWriteContracts, getErrorInfo, parseTotalCount } from '../lib/apiHelpers.mjs';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../components/ui/dialog';
import { Label } from '../components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Skeleton } from '../components/ui/skeleton';
import {
  Collapsible,
  CollapsibleContent,
} from '../components/ui/collapsible';
import {
  FileText,
  Upload,
  Search,
  Filter,
  Loader2,
  Plus,
  Trash2,
  Eye,
  X,
  Download,
  ChevronDown,
  Files,
} from 'lucide-react';
import { toast } from 'sonner';

const RiskBadge = ({ level }) => {
  const colors = {
    high: 'bg-red-500/10 text-red-500 border-red-500/20',
    medium: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
    low: 'bg-green-500/10 text-green-500 border-green-500/20',
  };
  return (
    <Badge variant="outline" className={colors[level] || 'bg-gray-500/10 text-gray-500'}>
      {level || 'N/A'}
    </Badge>
  );
};

const StatusBadge = ({ status }) => {
  const colors = {
    active: 'bg-green-500/10 text-green-500',
    draft: 'bg-blue-500/10 text-blue-500',
    expired: 'bg-gray-500/10 text-gray-500',
    pending: 'bg-yellow-500/10 text-yellow-500',
    review: 'bg-orange-500/10 text-orange-500',
    approved: 'bg-emerald-500/10 text-emerald-500',
  };
  const labels = {
    review: 'In Review',
    approved: 'Approved',
  };
  return (
    <Badge variant="secondary" className={colors[status] || colors.draft}>
      {labels[status] || status || 'draft'}
    </Badge>
  );
};

const CONTRACT_TYPES = [
  'General',
  'NDA',
  'Employment',
  'Service Agreement',
  'Lease',
  'Partnership',
  'Licensing',
  'Purchase Agreement',
  'Other',
];

const isSet = (value) => Boolean(value) && value !== 'all';

// Only API-provided messages for client errors (e.g. a size or type limit)
// are shown as the second line; nothing is invented for other failures.
export const UPLOAD_FAILED_BODY =
  'LexiSense reads digital PDFs with selectable text (like ones exported from Word or Google Docs) and .txt files. Check the file and try again.';

export function uploadErrorDetails(error) {
  const info = getErrorInfo(error);
  const isClientError = info.status !== null && info.status >= 400 && info.status < 500;
  return { apiMessage: isClientError && error?.response?.data ? info.message : null };
}

/**
 * Contracts list with no rows: first run (no contracts), an "expiring within"
 * filter with nothing in that window, or search/filters that match nothing.
 */
export function ContractsEmptyState({ searchQuery, filters, canWrite, onUpload, onClearFilters }) {
  const { statusFilter, typeFilter, riskFilter, expiringFilter } = filters;
  const otherFilters = [statusFilter, typeFilter, riskFilter].some(isSet);
  const hasExpiring = isSet(expiringFilter);

  if (!searchQuery && !otherFilters && !hasExpiring) {
    return (
      <EmptyState
        illustration={NoContractsIllustration}
        title="No contracts yet"
        data-testid="contracts-empty"
        action={
          canWrite ? (
            <Button onClick={onUpload}>
              <Upload className="mr-2 h-4 w-4" />
              Upload a contract
            </Button>
          ) : null
        }
      >
        <p>
          Upload your first contract and LexiSense will pull out the key terms and flag higher-risk contracts.
        </p>
      </EmptyState>
    );
  }

  if (hasExpiring && !searchQuery && !otherFilters) {
    return (
      <EmptyState
        illustration={NothingExpiringIllustration}
        title="Nothing expiring soon"
        data-testid="contracts-nothing-expiring"
        action={
          <Button variant="outline" onClick={onClearFilters}>
            View all contracts
          </Button>
        }
      >
        <p>No contracts expire in the next {expiringFilter} days. Upcoming renewals will show up here.</p>
      </EmptyState>
    );
  }

  return (
    <EmptyState
      illustration={NoSearchResultsIllustration}
      title="No matching contracts"
      data-testid="contracts-no-results"
      action={
        <Button variant="outline" onClick={onClearFilters}>
          Clear filters
        </Button>
      }
    >
      <p>
        {searchQuery
          ? `Nothing matches \u201c${searchQuery}\u201d. Search looks at contract titles and counterparties, so try a different word or clear your filters.`
          : 'No contracts match these filters. Try changing them, or clear them to see all your contracts.'}
      </p>
    </EmptyState>
  );
}

export default function ContractsPage() {
  const { user } = useAuth();
  const canWrite = canWriteContracts(user?.role);
  const [contracts, setContracts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [riskFilter, setRiskFilter] = useState('');
  const [expiringFilter, setExpiringFilter] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [bulkUploadOpen, setBulkUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  // { apiMessage } when the last single upload failed; shows the upload-failed state.
  const [uploadError, setUploadError] = useState(null);
  const [loadError, setLoadError] = useState(false);
  const [bulkUploading, setBulkUploading] = useState(false);
  const [bulkFiles, setBulkFiles] = useState([]);
  const [bulkContractType, setBulkContractType] = useState('General');
  const {
    page, pageSize, total, totalPages, offset, setTotal, goToPage, setPageSize, reset,
  } = usePagination({ pageSize: 25 });
  const [uploadData, setUploadData] = useState({
    title: '',
    counterparty: '',
    contractType: 'General',
    file: null,
  });

  const fetchContracts = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const params = { limit: pageSize, offset };
      if (searchQuery) params.search = searchQuery;
      if (statusFilter && statusFilter !== 'all') params.status_filter = statusFilter;
      if (typeFilter && typeFilter !== 'all') params.contract_type = typeFilter;
      if (riskFilter && riskFilter !== 'all') params.risk_level = riskFilter;
      if (expiringFilter && expiringFilter !== 'all') params.expiring_within = parseInt(expiringFilter);

      const response = await contractsAPI.list(params);
      setContracts(response.data);
      // The backend reports the full match count in X-Total-Count so the
      // pagination controls never have to guess from a truncated page.
      setTotal(parseTotalCount(response.headers) ?? response.data.length);
    } catch (error) {
      // Show the "something went wrong" state instead of an empty list, which
      // would look like an account with no contracts.
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [searchQuery, statusFilter, typeFilter, riskFilter, expiringFilter, pageSize, offset, setTotal]);

  useEffect(() => {
    fetchContracts();
  }, [fetchContracts]);

  // Any filter change invalidates the current page: go back to page 1 so the
  // user is never stranded on an out-of-range page after narrowing results.
  useEffect(() => {
    reset();
  }, [searchQuery, statusFilter, typeFilter, riskFilter, expiringFilter, reset]);

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!uploadData.file) {
      toast.error('Please select a file');
      return;
    }

    setUploading(true);
    const formData = new FormData();
    formData.append('file', uploadData.file);
    formData.append('title', uploadData.title);
    formData.append('counterparty', uploadData.counterparty);
    formData.append('contractType', uploadData.contractType);

    try {
      await contractsAPI.upload(formData);
      toast.success('Contract uploaded and analyzed successfully!');
      setUploadOpen(false);
      setUploadData({ title: '', counterparty: '', contractType: 'General', file: null });
      fetchContracts();
    } catch (error) {
      setUploadError(uploadErrorDetails(error));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id, title) => {
    if (!window.confirm(`Delete "${title}"? This action cannot be undone.`)) return;
    
    try {
      await contractsAPI.delete(id);
      toast.success('Contract deleted');
      fetchContracts();
    } catch (error) {
      toast.error(getErrorInfo(error).message || 'Delete failed');
    }
  };

  const handleBulkUpload = async (e) => {
    e.preventDefault();
    if (bulkFiles.length === 0) {
      toast.error('Please select files');
      return;
    }

    setBulkUploading(true);
    const formData = new FormData();
    bulkFiles.forEach((file) => formData.append('files', file));
    formData.append('contractType', bulkContractType);

    try {
      const response = await contractsAPI.bulkUpload(formData);
      const { successful, failed } = response.data;
      toast.success(`${successful} uploaded, ${failed} failed`);
      setBulkUploadOpen(false);
      setBulkFiles([]);
      fetchContracts();
    } catch (error) {
      toast.error('Bulk upload failed');
    } finally {
      setBulkUploading(false);
    }
  };

  const handleExportPDF = async (contractId, title) => {
    try {
      const response = await exportAPI.contractPDF(contractId);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${title.replace(/\s+/g, '_')}_report.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success('PDF downloaded');
    } catch (error) {
      toast.error('Failed to export PDF');
    }
  };

  const clearFilters = () => {
    setSearchQuery('');
    setStatusFilter('');
    setTypeFilter('');
    setRiskFilter('');
    setExpiringFilter('');
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString();
  };

  const formatFileSize = (bytes) => {
    if (!bytes) return '-';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <Layout>
      <div className="space-y-6" data-testid="contracts-page">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Contracts</h1>
            <p className="text-muted-foreground mt-1">Manage and analyze your contracts</p>
          </div>
          {canWrite && (
          <div className="flex gap-2">
            {/* Bulk Upload */}
            <Dialog open={bulkUploadOpen} onOpenChange={setBulkUploadOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" data-testid="bulk-upload-btn">
                  <Files className="mr-2 h-4 w-4" />
                  Bulk Upload
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Bulk Upload Contracts</DialogTitle>
                  <DialogDescription>Upload multiple files at once (max 10)</DialogDescription>
                </DialogHeader>
                <form onSubmit={handleBulkUpload} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="bulkContractType">Contract Type (for all files)</Label>
                    <Select value={bulkContractType} onValueChange={setBulkContractType}>
                      <SelectTrigger id="bulkContractType">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CONTRACT_TYPES.map((type) => (
                          <SelectItem key={type} value={type}>{type}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>Select Files (PDF or TXT)</Label>
                    <div className="border-2 border-dashed border-border rounded-lg p-6 text-center">
                      {bulkFiles.length > 0 ? (
                        <div className="space-y-2">
                          {bulkFiles.map((file, i) => (
                            <div key={i} className="flex items-center justify-between text-sm">
                              <span>{file.name}</span>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => setBulkFiles(bulkFiles.filter((_, j) => j !== i))}
                              >
                                <X className="h-4 w-4" />
                              </Button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <label className="cursor-pointer">
                          <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                          <p className="text-sm text-muted-foreground">Click to select files</p>
                          <input
                            type="file"
                            accept=".pdf,.txt"
                            multiple
                            className="hidden"
                            onChange={(e) => setBulkFiles(Array.from(e.target.files).slice(0, 10))}
                          />
                        </label>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <Button type="button" variant="outline" className="flex-1" onClick={() => setBulkUploadOpen(false)}>
                      Cancel
                    </Button>
                    <Button type="submit" className="flex-1" disabled={bulkUploading || bulkFiles.length === 0}>
                      {bulkUploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Upload {bulkFiles.length} Files
                    </Button>
                  </div>
                </form>
              </DialogContent>
            </Dialog>
            
            {/* Single Upload */}
            <Dialog
              open={uploadOpen}
              onOpenChange={(open) => {
                setUploadOpen(open);
                if (!open) setUploadError(null);
              }}
            >
              <DialogTrigger asChild>
                <Button data-testid="upload-contract-btn">
                  <Plus className="mr-2 h-4 w-4" />
                  Upload Contract
                </Button>
              </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Upload Contract</DialogTitle>
                <DialogDescription>
                  Upload a PDF or text file to analyze with AI
                </DialogDescription>
              </DialogHeader>
              {uploadError ? (
                <EmptyState
                  illustration={UploadFailedIllustration}
                  title="That file didn't upload"
                  role="alert"
                  size="sm"
                  data-testid="upload-failed"
                  action={
                    <Button
                      type="button"
                      onClick={() => {
                        setUploadError(null);
                        setUploadData({ ...uploadData, file: null });
                      }}
                    >
                      Choose another file
                    </Button>
                  }
                >
                  <p>{UPLOAD_FAILED_BODY}</p>
                  {uploadError.apiMessage && <p data-testid="upload-failed-detail">{uploadError.apiMessage}</p>}
                </EmptyState>
              ) : (
              <form onSubmit={handleUpload} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="title">Contract Title *</Label>
                  <Input
                    id="title"
                    placeholder="e.g., Service Agreement - Acme Corp"
                    value={uploadData.title}
                    onChange={(e) => setUploadData({ ...uploadData, title: e.target.value })}
                    required
                    data-testid="upload-title"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="counterparty">Counterparty</Label>
                  <Input
                    id="counterparty"
                    placeholder="e.g., Acme Corporation"
                    value={uploadData.counterparty}
                    onChange={(e) => setUploadData({ ...uploadData, counterparty: e.target.value })}
                    data-testid="upload-counterparty"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="contractType">Contract Type</Label>
                  <Select
                    value={uploadData.contractType}
                    onValueChange={(value) => setUploadData({ ...uploadData, contractType: value })}
                  >
                    <SelectTrigger id="contractType" data-testid="upload-type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CONTRACT_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="file">File (PDF or TXT) *</Label>
                  <div className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:border-primary/50 transition-colors">
                    {uploadData.file ? (
                      <div className="flex items-center justify-center gap-2">
                        <FileText className="h-5 w-5 text-primary" />
                        <span className="text-sm font-medium">{uploadData.file.name}</span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          onClick={() => setUploadData({ ...uploadData, file: null })}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ) : (
                      <label className="cursor-pointer">
                        <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                        <p className="text-sm text-muted-foreground">
                          Click to upload or drag and drop
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">PDF, TXT (max 10MB)</p>
                        <input
                          type="file"
                          accept=".pdf,.txt,application/pdf,text/plain"
                          className="hidden"
                          onChange={(e) => setUploadData({ ...uploadData, file: e.target.files[0] })}
                          data-testid="upload-file"
                        />
                      </label>
                    )}
                  </div>
                </div>
                <div className="flex gap-3 pt-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="flex-1"
                    onClick={() => setUploadOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" className="flex-1" disabled={uploading} data-testid="upload-submit">
                    {uploading ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Analyzing...
                      </>
                    ) : (
                      'Upload & Analyze'
                    )}
                  </Button>
                </div>
              </form>
              )}
            </DialogContent>
          </Dialog>
          </div>
          )}
        </div>

        {/* Filters */}
        <Card>
          <CardContent className="p-4">
            <div className="flex flex-col gap-4">
              <div className="flex flex-col sm:flex-row gap-4">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search contracts..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-10"
                    data-testid="search-input"
                  />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                  <SelectTrigger className="w-full sm:w-40" data-testid="status-filter" aria-label="Filter by status">
                    <Filter className="h-4 w-4 mr-2" aria-hidden="true" />
                    <SelectValue placeholder="Status" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Status</SelectItem>
                    <SelectItem value="draft">Draft</SelectItem>
                    <SelectItem value="review">In Review</SelectItem>
                    <SelectItem value="approved">Approved</SelectItem>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="expired">Expired</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={typeFilter} onValueChange={setTypeFilter}>
                  <SelectTrigger className="w-full sm:w-40" data-testid="type-filter" aria-label="Filter by type">
                    <SelectValue placeholder="Type" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Types</SelectItem>
                    {CONTRACT_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {/* The trigger and panel live in different parts of the layout, so the toggle points
                    aria-controls at the always-mounted Collapsible root rather than using a second Root
                    whose content never renders (that left aria-controls pointing at a missing id). */}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-expanded={advancedOpen}
                  aria-controls="advanced-filters"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  data-testid="advanced-filters-toggle"
                >
                  <ChevronDown aria-hidden="true" className={`h-4 w-4 mr-1 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
                  Advanced
                </Button>
              </div>
              
              <Collapsible id="advanced-filters" open={advancedOpen} onOpenChange={setAdvancedOpen}>
                <CollapsibleContent className="pt-4 border-t">
                  <div className="flex flex-col sm:flex-row gap-4">
                    <Select value={riskFilter} onValueChange={setRiskFilter}>
                      <SelectTrigger className="w-full sm:w-40" data-testid="risk-filter" aria-label="Filter by risk level">
                        <SelectValue placeholder="Risk Level" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Risk Levels</SelectItem>
                        <SelectItem value="high">High Risk</SelectItem>
                        <SelectItem value="medium">Medium Risk</SelectItem>
                        <SelectItem value="low">Low Risk</SelectItem>
                      </SelectContent>
                    </Select>
                    <Select value={expiringFilter} onValueChange={setExpiringFilter}>
                      <SelectTrigger className="w-full sm:w-48" data-testid="expiring-filter" aria-label="Filter by expiry date">
                        <SelectValue placeholder="Expiring Within" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Contracts</SelectItem>
                        <SelectItem value="7">Expiring in 7 days</SelectItem>
                        <SelectItem value="30">Expiring in 30 days</SelectItem>
                        <SelectItem value="60">Expiring in 60 days</SelectItem>
                        <SelectItem value="90">Expiring in 90 days</SelectItem>
                      </SelectContent>
                    </Select>
                    <Button variant="ghost" size="sm" onClick={clearFilters}>
                      Clear All Filters
                    </Button>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </div>
          </CardContent>
        </Card>

        {/* Contracts Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-8 space-y-4">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : loadError ? (
              <EmptyState
                illustration={SomethingWentWrongIllustration}
                title="Something went wrong"
                role="alert"
                data-testid="contracts-load-error"
                action={<Button onClick={fetchContracts}>Try again</Button>}
              >
                <p>We couldn't load this just now. Try again in a moment.</p>
              </EmptyState>
            ) : contracts.length === 0 ? (
              <ContractsEmptyState
                searchQuery={searchQuery}
                filters={{ statusFilter, typeFilter, riskFilter, expiringFilter }}
                canWrite={canWrite}
                onUpload={() => setUploadOpen(true)}
                onClearFilters={clearFilters}
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Title</TableHead>
                    <TableHead>Counterparty</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Risk</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {contracts.map((contract) => (
                    <TableRow key={contract.id}>
                      <TableCell>
                        <Link
                          to={`/contracts/${contract.id}`}
                          className="font-medium hover:text-primary transition-colors"
                        >
                          {contract.title}
                        </Link>
                        {contract.fileName && (
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {contract.fileName} ({formatFileSize(contract.fileSize)})
                          </p>
                        )}
                      </TableCell>
                      <TableCell>{contract.counterparty || '-'}</TableCell>
                      <TableCell>{contract.contractType}</TableCell>
                      <TableCell>
                        <StatusBadge status={contract.status} />
                      </TableCell>
                      <TableCell>
                        <RiskBadge level={contract.riskLevel} />
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDate(contract.createdAt)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button variant="ghost" size="icon" asChild>
                            <Link
                              to={`/contracts/${contract.id}`}
                              data-testid={`view-${contract.id}`}
                              aria-label={`View ${contract.title}`}
                            >
                              <Eye className="h-4 w-4" aria-hidden="true" />
                            </Link>
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleExportPDF(contract.id, contract.title)}
                            title="Export PDF"
                            aria-label={`Export ${contract.title} as PDF`}
                          >
                            <Download className="h-4 w-4" aria-hidden="true" />
                          </Button>
                          {canWrite && (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDelete(contract.id, contract.title)}
                            data-testid={`delete-${contract.id}`}
                            aria-label={`Delete ${contract.title}`}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" aria-hidden="true" />
                          </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
          <PaginationBar
            page={page}
            totalPages={totalPages}
            total={total}
            pageSize={pageSize}
            onPageChange={goToPage}
            onPageSizeChange={setPageSize}
            loading={loading}
            itemLabel="contracts"
          />
        </Card>
      </div>
    </Layout>
  );
}
